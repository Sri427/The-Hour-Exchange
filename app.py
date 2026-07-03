import os
import math
import datetime
import random
import functools
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, jsonify
from models import (db, GoodNeighbor, ActOfService, NeighborVouchPool, HoursExchanged,
                    Review, SkillEndorsement, TimeBundle, TimeBundleContribution,
                    FraudLog, FlagReport, User, Task, Vouch, EscrowTransaction)
from database import init_db

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hour-exchange-secret-key-2026-prod')

if os.environ.get('CHRONO_TESTING') == 'true':
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['TESTING'] = True
else:
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrono.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

if os.environ.get('CHRONO_TESTING') != 'true':
    init_db(app)

# --- constants ---
BALANCE_FLOOR = -3
WEEKLY_EARN_CAP = 5
SWAP_DEADLINE_HOURS = 48

# simple IP rate limiter for registration
_ip_register_log = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check_ip_rate_limit(ip):
    now = datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(hours=1)
    times = [t for t in _ip_register_log.get(ip, []) if t > cutoff]
    _ip_register_log[ip] = times
    return len(times) < 3

def _record_ip_register(ip):
    _ip_register_log.setdefault(ip, []).append(datetime.datetime.utcnow())


def _get_global_hours():
    return int(db.session.query(db.func.sum(HoursExchanged.amount))
               .filter(HoursExchanged.status == 'Released').scalar() or 0)


def _auto_expire_stale_swaps():
    """
    Finds swaps where the 48h confirmation window expired and refunds tokens.
    Also tracks who ghosted (didn't confirm) so reliability scores take a hit.
    """
    now = datetime.datetime.utcnow()
    stale = HoursExchanged.query.filter(
        HoursExchanged.status == 'Locked',
        HoursExchanged.deadline != None,
        HoursExchanged.deadline < now
    ).all()

    for escrow in stale:
        svc = escrow.task
        if not svc or svc.status != 'Accepted':
            continue

        # figure out who ghosted and penalize them
        if escrow.payer_confirmed and not escrow.payee_confirmed:
            ghoster = db.session.get(GoodNeighbor, escrow.payee_id)
            if ghoster:
                ghoster.ghost_confirmation_count += 1
        elif escrow.payee_confirmed and not escrow.payer_confirmed:
            ghoster = db.session.get(GoodNeighbor, escrow.payer_id)
            if ghoster:
                ghoster.ghost_confirmation_count += 1
        # if neither confirmed, both kind of flaked but we don't double penalize

        payer = db.session.get(GoodNeighbor, escrow.payer_id)
        if payer:
            payer.time_tokens += escrow.amount
        escrow.status = 'Refunded'
        svc.status = 'Cancelled'

    if stale:
        db.session.commit()


def _run_fraud_check(escrow):
    """
    Background check that runs every time a swap completes.
    Looks for:
      1. Velocity: too many exchanges between the same two people in 7 days
      2. New account: first exchange on a brand new account (could be sockpuppet)
      3. Single party concentration: 85%+ of swaps with one person

    Big O: O(T) for the velocity query where T = transactions in the 7-day window
    Logs to FraudLog for admin review — doesn't block the transaction.
    """
    VELOCITY_THRESHOLD = 4
    SINGLE_PARTY_RATIO = 0.85

    one_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)

    # check 1: velocity between these two users this week
    velocity = HoursExchanged.query.filter(
        HoursExchanged.status == 'Released',
        db.or_(
            db.and_(HoursExchanged.payer_id == escrow.payer_id, HoursExchanged.payee_id == escrow.payee_id),
            db.and_(HoursExchanged.payer_id == escrow.payee_id, HoursExchanged.payee_id == escrow.payer_id)
        ),
        HoursExchanged.created_at >= one_week_ago
    ).count()

    if velocity > VELOCITY_THRESHOLD:
        _log_fraud(escrow.payer_id, 'velocity',
                   f'{velocity} mutual exchanges in 7 days with user {escrow.payee_id}')

    # check 2: new account (less than 3 total completed exchanges ever)
    payer_total = HoursExchanged.query.filter(
        db.or_(HoursExchanged.payer_id == escrow.payer_id,
               HoursExchanged.payee_id == escrow.payer_id),
        HoursExchanged.status == 'Released'
    ).count()

    if payer_total <= 1:
        _log_fraud(escrow.payer_id, 'new_account',
                   f'user {escrow.payer_id} just finished their first exchange ever')

    # check 3: single counterparty concentration
    total_for_payee = HoursExchanged.query.filter(
        db.or_(HoursExchanged.payer_id == escrow.payee_id,
               HoursExchanged.payee_id == escrow.payee_id),
        HoursExchanged.status == 'Released'
    ).count()

    if total_for_payee >= 5:
        top_partner_count = HoursExchanged.query.filter(
            db.or_(
                db.and_(HoursExchanged.payer_id == escrow.payee_id, HoursExchanged.payee_id == escrow.payer_id),
                db.and_(HoursExchanged.payer_id == escrow.payer_id, HoursExchanged.payee_id == escrow.payee_id)
            ),
            HoursExchanged.status == 'Released'
        ).count()

        if top_partner_count / total_for_payee > SINGLE_PARTY_RATIO:
            _log_fraud(escrow.payee_id, 'single_party',
                       f'{top_partner_count}/{total_for_payee} exchanges with same user')


def _log_fraud(user_id, flag_type, details):
    """only logs if we haven't already flagged this recently"""
    one_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    existing = FraudLog.query.filter_by(
        user_id=user_id, flag_type=flag_type, resolved=False
    ).filter(FraudLog.created_at >= one_week_ago).first()

    if not existing:
        db.session.add(FraudLog(user_id=user_id, flag_type=flag_type, details=details))


def _match_neighbors(need_task, limit=3):
    """
    Weighted scoring across 4 variables to find the best helpers for a request.

    Scoring formula:
      skill_score   (0-40 pts): endorsement count in the matching category
      prox_score    (0-20 pts): how close their last task was on the canvas map
      rel_score     (0-30 pts): their reliability % mapped to 0-30
      activity_score(0-10 pts): completed tasks in the last 30 days

    Total max = 100 pts, sorted descending.

    Big O: O(U log U) where U = number of users
      - scanning all users: O(U)
      - scoring each user: O(1) per user (with indexed queries)
      - sorting: O(U log U)
    """
    all_users = GoodNeighbor.query.filter(
        GoodNeighbor.id != need_task.creator_id,
        GoodNeighbor.email_verified == True
    ).all()

    thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    scored = []

    for usr in all_users:
        # 1. skill overlap
        endorse = SkillEndorsement.query.filter_by(
            user_id=usr.id, skill_name=need_task.category
        ).first()
        skill_score = min((endorse.endorsement_count if endorse else 0) * 8, 40)

        # 2. proximity — based on their most recent task location
        last_task = ActOfService.query.filter(
            (ActOfService.provider_id == usr.id) | (ActOfService.creator_id == usr.id)
        ).order_by(ActOfService.created_at.desc()).first()

        if last_task:
            dlat = last_task.lat - need_task.lat
            dlng = last_task.lng - need_task.lng
            dist = math.sqrt(dlat**2 + dlng**2)
            prox_score = max(0.0, 20.0 - (dist / 943.0) * 20.0)
        else:
            prox_score = 10.0  # unknown, give middle

        # 3. reliability
        rel_score = (usr.reliability_score / 100.0) * 30.0

        # 4. recent activity
        recent = ActOfService.query.filter(
            ActOfService.status == 'Completed',
            (ActOfService.provider_id == usr.id) | (ActOfService.receiver_id == usr.id),
            ActOfService.created_at >= thirty_days_ago
        ).count()
        activity_score = min(recent * 2, 10)

        total = skill_score + prox_score + rel_score + activity_score
        scored.append((round(total, 1), usr))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def _build_neighbor_graph():
    """
    Builds adjacency dict: user_id -> set of user_ids they've completed a swap with.
    Used for second-degree neighbor suggestions.

    Big O: O(E) where E = completed exchanges
    """
    graph = {}
    exchanges = HoursExchanged.query.filter_by(status='Released').all()
    for ex in exchanges:
        graph.setdefault(ex.payer_id, set()).add(ex.payee_id)
        graph.setdefault(ex.payee_id, set()).add(ex.payer_id)
    return graph


def _get_second_degree(user_id, skill_category=None):
    """
    BFS to depth 2 — finds neighbors of neighbors who haven't yet swapped with this user.
    Optionally filters by skill category.

    Big O: O(V + E) for the BFS traversal, where V=users and E=edges (swaps)
    """
    graph = _build_neighbor_graph()
    first_degree = graph.get(user_id, set())
    second_degree = set()

    for n1 in first_degree:
        for n2 in graph.get(n1, set()):
            if n2 != user_id and n2 not in first_degree:
                second_degree.add(n2)

    results = []
    for uid in second_degree:
        usr = db.session.get(GoodNeighbor, uid)
        if not usr:
            continue
        if skill_category:
            has_skill = SkillEndorsement.query.filter_by(
                user_id=uid, skill_name=skill_category
            ).first()
            if not has_skill:
                continue
        results.append(usr)

    return results


# ---------------------------------------------------------------------------
# auth middleware
# ---------------------------------------------------------------------------

@app.before_request
def load_logged_in_user():
    uid = session.get('user_id')
    g.user = db.session.get(GoodNeighbor, uid) if uid else None


def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'message': 'Authentication required.'}), 401
            flash('Please log in to enter The Town Square.', 'warning')
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

api_login_required = login_required


@app.context_processor
def inject_global_metrics():
    if g.user:
        released = db.session.query(db.func.sum(HoursExchanged.amount))\
            .filter(HoursExchanged.status == 'Released').scalar() or 0
        badge_label, badge_icon = g.user.badge
        return {
            'current_user_tokens': g.user.time_tokens,
            'current_user_reliability': g.user.reliability_score,
            'current_user_badge': badge_label,
            'current_user_badge_icon': badge_icon,
            'global_exchanged_hours': int(released)
        }
    return {
        'current_user_tokens': 0,
        'current_user_reliability': 100.0,
        'current_user_badge': 'New Member',
        'current_user_badge_icon': 'seedling',
        'global_exchanged_hours': 0
    }


# ---------------------------------------------------------------------------
# auth routes
# ---------------------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('index'))

    step = request.args.get('step', '1')

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not username or not email or not password:
            flash('We missed a detail — can you fill in all three fields?', 'error')
            return render_template('register.html', step=step)

        # IP rate limit: stops someone making 50 accounts in a minute
        client_ip = request.remote_addr or '0.0.0.0'
        if not _check_ip_rate_limit(client_ip):
            flash('Too many accounts from this connection right now. Try again in an hour.', 'error')
            return render_template('register.html', step=step)

        existing = GoodNeighbor.query.filter(
            (GoodNeighbor.username == username) | (GoodNeighbor.email == email)
        ).first()
        if existing:
            flash('That username or email is already taken — try a different one!', 'error')
            return render_template('register.html', step=step)

        new_neighbor = GoodNeighbor(username=username, email=email, time_tokens=10, email_verified=True)
        new_neighbor.set_password(password)
        db.session.add(new_neighbor)
        db.session.commit()

        _record_ip_register(client_ip)
        session['user_id'] = new_neighbor.id
        flash("Welcome to the neighborhood! We've added 10 Hour Tokens to get you started.", 'success')
        return redirect(url_for('index'))

    return render_template('register.html', step=step)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        usr = GoodNeighbor.query.filter_by(username=username).first()
        if usr and usr.check_password(password):
            session['user_id'] = usr.id
            flash('Welcome back, neighbor!', 'success')
            return redirect(url_for('index'))
        flash("Hmm, that didn't match. Double-check your username and password.", 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('See you around the neighborhood!', 'success')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# page routes
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def index():
    _auto_expire_stale_swaps()
    open_services = ActOfService.query.filter_by(status='Open').order_by(ActOfService.created_at.desc()).all()
    categories = ['All', 'Learning & Growing', 'Lending a Hand', 'Tech Help', 'Everyday Errands', 'Creative Arts']
    return render_template('index.html', tasks=open_services, categories=categories)


@app.route('/profile/<int:user_id>')
@login_required
def profile(user_id):
    nbr = db.session.get(GoodNeighbor, user_id)
    if not nbr:
        flash('We could not find that neighbor.', 'error')
        return redirect(url_for('index'))

    vouches_count = db.session.query(NeighborVouchPool)\
        .join(GoodNeighbor, NeighborVouchPool.voucher_id == GoodNeighbor.id)\
        .filter(NeighborVouchPool.vouchee_id == nbr.id, GoodNeighbor.verification_tier == 2).count()

    already_vouched = False
    if g.user.id != nbr.id:
        already_vouched = NeighborVouchPool.query.filter_by(
            voucher_id=g.user.id, vouchee_id=nbr.id
        ).first() is not None

    # single-counterparty velocity flag
    velocity_flag = False
    total_ex = HoursExchanged.query.filter(
        (HoursExchanged.payer_id == nbr.id) | (HoursExchanged.payee_id == nbr.id),
        HoursExchanged.status == 'Released'
    ).count()
    if total_ex >= 4:
        from sqlalchemy import case, func as sqlfunc
        cp_col = case((HoursExchanged.payer_id == nbr.id, HoursExchanged.payee_id),
                      else_=HoursExchanged.payer_id)
        top = db.session.query(cp_col, sqlfunc.count().label('cnt'))\
            .filter(
                (HoursExchanged.payer_id == nbr.id) | (HoursExchanged.payee_id == nbr.id),
                HoursExchanged.status == 'Released'
            ).group_by(cp_col).order_by(sqlfunc.count().desc()).first()
        if top and top.cnt / total_ex > 0.8:
            velocity_flag = True

    # second-degree neighbor suggestions
    second_deg = _get_second_degree(nbr.id)[:3]

    endorsements = SkillEndorsement.query.filter_by(user_id=nbr.id)\
        .order_by(SkillEndorsement.endorsement_count.desc()).all()
    reviews = Review.query.filter_by(reviewee_id=nbr.id)\
        .order_by(Review.created_at.desc()).all()

    return render_template('profile.html', profile_user=nbr, vouches_count=vouches_count,
                           already_vouched=already_vouched, endorsements=endorsements,
                           reviews=reviews, velocity_flag=velocity_flag, second_deg=second_deg)


@app.route('/tasks')
@login_required
def tasks_hub():
    _auto_expire_stale_swaps()

    active_swaps = ActOfService.query.filter(
        (ActOfService.status == 'Accepted') &
        ((ActOfService.provider_id == g.user.id) | (ActOfService.receiver_id == g.user.id))
    ).all()

    my_listings = ActOfService.query.filter(
        (ActOfService.status == 'Open') & (ActOfService.creator_id == g.user.id)
    ).all()

    history = ActOfService.query.filter(
        ((ActOfService.status == 'Completed') | (ActOfService.status == 'Cancelled')) &
        ((ActOfService.creator_id == g.user.id) | (ActOfService.provider_id == g.user.id) | (ActOfService.receiver_id == g.user.id))
    ).order_by(ActOfService.scheduled_at.desc()).all()

    reviewable = []
    for svc in history:
        if svc.status == 'Completed':
            if not Review.query.filter_by(task_id=svc.id, reviewer_id=g.user.id).first():
                reviewable.append(svc)

    return render_template('tasks.html', active=active_swaps, listings=my_listings,
                           history=history, reviewable=reviewable)


# ---------------------------------------------------------------------------
# API: stats + tasks
# ---------------------------------------------------------------------------

@app.route('/api/tasks')
@login_required
def api_get_tasks():
    open_services = ActOfService.query.filter_by(status='Open').order_by(ActOfService.created_at.desc()).all()
    return jsonify([s.to_dict() for s in open_services])


@app.route('/api/user/me')
@login_required
def api_user_me():
    badge_label, badge_icon = g.user.badge
    return jsonify({
        'id': g.user.id,
        'username': g.user.username,
        'tokens': g.user.time_tokens,
        'reliability': g.user.reliability_score,
        'tier': g.user.verification_tier,
        'badge': badge_label,
        'badge_icon': badge_icon
    })


@app.route('/api/stats/global')
@login_required
def api_stats_global():
    return jsonify({'global_hours': _get_global_hours()})


@app.route('/api/stats/impact')
@login_required
def api_stats_impact():
    """Community impact numbers for the SDG dashboard on the homepage."""
    hours = _get_global_hours()

    skills_shared = db.session.query(ActOfService.category)\
        .filter(ActOfService.status == 'Completed').distinct().count()

    neighbors_connected = db.session.query(HoursExchanged.payer_id)\
        .filter(HoursExchanged.status == 'Released')\
        .union(db.session.query(HoursExchanged.payee_id).filter(HoursExchanged.status == 'Released'))\
        .count()

    return jsonify({
        'total_hours': hours,
        'skills_shared': skills_shared,
        'neighbors_connected': neighbors_connected
    })


@app.route('/api/task/create', methods=['POST'])
@login_required
def api_create_task():
    if not g.user.email_verified:
        return jsonify({'success': False, 'message': 'Please verify your email before posting.'}), 403

    data = request.get_json(silent=True) or request.form
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    task_type = (data.get('task_type') or '').upper()
    category = (data.get('category') or '').strip()
    urgency = (data.get('urgency') or 'Medium').strip()
    tok = int(data.get('tokens_value', 1))
    scheduled_str = data.get('scheduled_at', '')
    is_bundle = str(data.get('is_bundle', 'false')).lower() == 'true'

    if not title or not description or task_type not in ['REQUEST', 'OFFER'] or not scheduled_str:
        return jsonify({'success': False, 'message': 'We missed a detail — can you check all the fields?'}), 400

    try:
        scheduled_at = datetime.datetime.fromisoformat(scheduled_str)
    except ValueError:
        return jsonify({'success': False, 'message': 'Something looks off with that date.'}), 400

    if scheduled_at < datetime.datetime.utcnow():
        return jsonify({'success': False, 'message': 'Please pick a future date and time.'}), 400

    if task_type == 'REQUEST' and (g.user.time_tokens - tok) < BALANCE_FLOOR:
        return jsonify({
            'success': False,
            'message': 'Your balance is getting low — share a skill first to earn some tokens back!'
        }), 400

    lat = round(random.uniform(50, 750), 1)
    lng = round(random.uniform(50, 450), 1)

    creator_id = g.user.id
    receiver_id = creator_id if task_type == 'REQUEST' else None
    provider_id = creator_id if task_type == 'OFFER' else None

    svc = ActOfService(
        title=title, description=description, task_type=task_type, category=category,
        urgency=urgency, tokens_value=tok, creator_id=creator_id,
        receiver_id=receiver_id, provider_id=provider_id,
        scheduled_at=scheduled_at, lat=lat, lng=lng, is_bundle=is_bundle
    )
    db.session.add(svc)
    db.session.flush()

    if is_bundle and task_type == 'REQUEST' and tok > 1:
        bundle = TimeBundle(task_id=svc.id, target_tokens=tok)
        db.session.add(bundle)

    db.session.commit()

    return jsonify({
        'success': True,
        'task': svc.to_dict(),
        'user_tokens': g.user.time_tokens,
        'message': "You're all set! Your listing is live on the board."
    })


# ---------------------------------------------------------------------------
# API: booking + confirmation
# ---------------------------------------------------------------------------

@app.route('/task/book/<int:task_id>', methods=['POST'])
@app.route('/api/task/book/<int:task_id>', methods=['POST'])
@login_required
def book_task(task_id):
    is_api = request.path.startswith('/api/')
    svc = db.session.get(ActOfService, task_id)

    if not svc or svc.status != 'Open':
        if is_api:
            return jsonify({'success': False, 'message': 'That listing is no longer available.'}), 404
        flash('That listing is no longer available.', 'error')
        return redirect(url_for('index'))

    # self-dealing block
    if svc.creator_id == g.user.id:
        if is_api:
            return jsonify({'success': False, 'message': 'You cannot book your own listing.'}), 400
        flash('You cannot book your own listing.', 'error')
        return redirect(url_for('index'))

    if svc.task_type == 'REQUEST':
        svc.provider_id = g.user.id
        payer_id = svc.receiver_id
        payee_id = g.user.id
    else:
        svc.receiver_id = g.user.id
        payer_id = g.user.id
        payee_id = svc.provider_id

    # explicit self-dealing guard on escrow side too
    if payer_id == payee_id:
        if is_api:
            return jsonify({'success': False, 'message': 'Cannot create a swap with yourself.'}), 400
        flash('Cannot create a swap with yourself.', 'error')
        return redirect(url_for('index'))

    payer = db.session.get(GoodNeighbor, payer_id)
    if (payer.time_tokens - svc.tokens_value) < BALANCE_FLOOR:
        msg = "The receiver's balance would go too low. They need to earn some tokens first!"
        if is_api:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, 'error')
        return redirect(url_for('index'))

    payer.time_tokens -= svc.tokens_value
    deadline = datetime.datetime.utcnow() + datetime.timedelta(hours=SWAP_DEADLINE_HOURS)

    escrow = HoursExchanged(
        task_id=svc.id, payer_id=payer_id, payee_id=payee_id,
        amount=svc.tokens_value, status='Locked',
        payer_confirmed=False, payee_confirmed=False,
        deadline=deadline
    )
    svc.status = 'Accepted'
    db.session.add(escrow)
    db.session.commit()

    if is_api:
        return jsonify({
            'success': True,
            'task_id': svc.id, 'status': 'Accepted',
            'user_tokens': g.user.time_tokens,
            'global_hours': _get_global_hours(),
            'message': "You're committed! Tokens are safely in the Holding Balance."
        })

    flash("You're committed! Tokens are safely in the Holding Balance.", 'success')
    return redirect(url_for('tasks_hub'))


@app.route('/task/confirm-completion/<int:task_id>', methods=['POST'])
@app.route('/api/task/confirm/<int:task_id>', methods=['POST'])
@login_required
def confirm_task(task_id):
    is_api = request.path.startswith('/api/')
    svc = db.session.get(ActOfService, task_id)

    if not svc or svc.status != 'Accepted':
        if is_api:
            return jsonify({'success': False, 'message': 'Cannot confirm this swap right now.'}), 400
        flash('Cannot confirm this swap right now.', 'error')
        return redirect(url_for('tasks_hub'))

    escrow = svc.escrow
    if not escrow or escrow.status != 'Locked':
        if is_api:
            return jsonify({'success': False, 'message': 'No holding balance found for this swap.'}), 400
        flash("We couldn't find a holding balance for this swap.", 'error')
        return redirect(url_for('tasks_hub'))

    # deadline check — if 48h passed, auto-cancel and refund
    if escrow.deadline and datetime.datetime.utcnow() > escrow.deadline:
        payer = db.session.get(GoodNeighbor, escrow.payer_id)
        if payer:
            payer.time_tokens += escrow.amount
        escrow.status = 'Refunded'
        svc.status = 'Cancelled'
        db.session.commit()
        msg = 'This swap timed out — the 48-hour confirmation window passed. Tokens have been refunded.'
        if is_api:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, 'warning')
        return redirect(url_for('tasks_hub'))

    if g.user.id == escrow.payer_id:
        escrow.payer_confirmed = True
    elif g.user.id == escrow.payee_id:
        escrow.payee_confirmed = True
    else:
        if is_api:
            return jsonify({'success': False, 'message': 'Not authorized to confirm this swap.'}), 403
        flash('Not authorized to confirm this swap.', 'error')
        return redirect(url_for('tasks_hub'))

    if escrow.payer_confirmed and escrow.payee_confirmed:
        payee = db.session.get(GoodNeighbor, escrow.payee_id)

        # ghost exchange weekly cap — prevents farming the same person
        one_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        already_earned = db.session.query(db.func.sum(HoursExchanged.amount)).filter(
            HoursExchanged.payee_id == escrow.payee_id,
            HoursExchanged.payer_id == escrow.payer_id,
            HoursExchanged.status == 'Released',
            HoursExchanged.created_at >= one_week_ago
        ).scalar() or 0

        if already_earned + escrow.amount > WEEKLY_EARN_CAP:
            payer = db.session.get(GoodNeighbor, escrow.payer_id)
            payer.time_tokens += escrow.amount
            escrow.status = 'Refunded'
            svc.status = 'Cancelled'
            db.session.commit()
            msg = f'Weekly cap reached — you can only earn {WEEKLY_EARN_CAP} tokens from the same neighbor per week. Tokens refunded.'
            if is_api:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'warning')
            return redirect(url_for('tasks_hub'))

        payee.time_tokens += escrow.amount
        escrow.status = 'Released'
        svc.status = 'Completed'

        payer = db.session.get(GoodNeighbor, escrow.payer_id)
        payer.completed_tasks_count += 1
        payee.completed_tasks_count += 1
        db.session.commit()

        # run background fraud check — doesn't block anything, just logs
        _run_fraud_check(escrow)
        db.session.commit()

        if is_api:
            return jsonify({
                'success': True, 'completed': True,
                'user_tokens': g.user.time_tokens,
                'global_hours': _get_global_hours(),
                'payer_confirmed': True, 'payee_confirmed': True,
                'message': 'Time well spent! Your hours have been shared safely.'
            })

        flash('Time well spent! Your hours have been shared safely.', 'success')
        return redirect(url_for('tasks_hub'))

    else:
        db.session.commit()
        if is_api:
            return jsonify({
                'success': True, 'completed': False,
                'user_tokens': g.user.time_tokens,
                'global_hours': _get_global_hours(),
                'payer_confirmed': escrow.payer_confirmed,
                'payee_confirmed': escrow.payee_confirmed,
                'message': "Got it! Waiting for your neighbor to confirm too."
            })

        flash("Got it! We'll complete the exchange once your neighbor confirms too.", 'info')
        return redirect(url_for('tasks_hub'))


@app.route('/task/cancel/<int:task_id>', methods=['POST'])
@app.route('/api/task/cancel/<int:task_id>', methods=['POST'])
@login_required
def cancel_task(task_id):
    is_api = request.path.startswith('/api/')
    svc = db.session.get(ActOfService, task_id)

    if not svc:
        if is_api:
            return jsonify({'success': False, 'message': 'Task not found.'}), 404
        flash("We couldn't find that listing.", 'error')
        return redirect(url_for('tasks_hub'))

    if g.user.id not in [svc.creator_id, svc.provider_id, svc.receiver_id]:
        if is_api:
            return jsonify({'success': False, 'message': 'Not authorized.'}), 403
        flash('Not authorized to cancel this.', 'error')
        return redirect(url_for('tasks_hub'))

    if svc.status == 'Open':
        svc.status = 'Cancelled'
        db.session.commit()
        if is_api:
            return jsonify({
                'success': True, 'user_tokens': g.user.time_tokens,
                'reliability': g.user.reliability_score, 'is_late': False,
                'message': 'Listing removed from the board.'
            })
        flash('Your listing has been removed from the board.', 'success')
        return redirect(url_for('tasks_hub'))

    if svc.status == 'Accepted':
        diff = svc.scheduled_at - datetime.datetime.utcnow()
        is_late = diff < datetime.timedelta(hours=24)
        if is_late:
            g.user.late_cancellations_count += 1

        escrow = svc.escrow
        if escrow and escrow.status == 'Locked':
            payer = db.session.get(GoodNeighbor, escrow.payer_id)
            payer.time_tokens += escrow.amount
            escrow.status = 'Refunded'

        svc.status = 'Cancelled'
        db.session.commit()

        if is_api:
            msg = 'Late cancellation noted — your Reliability Score has been adjusted.' if is_late else 'Cancelled. Tokens refunded.'
            return jsonify({
                'success': True, 'user_tokens': g.user.time_tokens,
                'reliability': g.user.reliability_score, 'is_late': is_late,
                'message': msg
            })

        if is_late:
            flash('Late cancellation noted. Your Reliability Score has been adjusted.', 'warning')
        else:
            flash('Exchange cancelled. Tokens have been refunded.', 'success')
        return redirect(url_for('tasks_hub'))

    if is_api:
        return jsonify({'success': False, 'message': 'Cannot cancel a completed exchange.'}), 400
    flash('Completed exchanges cannot be cancelled.', 'error')
    return redirect(url_for('tasks_hub'))


# ---------------------------------------------------------------------------
# API: matching + graph
# ---------------------------------------------------------------------------

@app.route('/api/match/<int:task_id>')
@login_required
def api_match(task_id):
    svc = db.session.get(ActOfService, task_id)
    if not svc:
        return jsonify({'success': False, 'message': 'Task not found.'}), 404

    matches = _match_neighbors(svc)
    result = []
    for score, usr in matches:
        badge_label, badge_icon = usr.badge
        result.append({
            'user_id': usr.id,
            'username': usr.username,
            'score': score,
            'reliability': usr.reliability_score,
            'badge': badge_label,
            'badge_icon': badge_icon,
            'tier': usr.verification_tier
        })

    return jsonify({'success': True, 'matches': result})


@app.route('/api/graph/suggest/<int:task_id>')
@login_required
def api_graph_suggest(task_id):
    """Second-degree neighbor suggestions for this task's skill category."""
    svc = db.session.get(ActOfService, task_id)
    if not svc:
        return jsonify({'success': False, 'message': 'Task not found.'}), 404

    suggestions = _get_second_degree(g.user.id, skill_category=svc.category)[:3]
    result = [{'user_id': u.id, 'username': u.username, 'badge': u.badge[0]} for u in suggestions]
    return jsonify({'success': True, 'suggestions': result})


# ---------------------------------------------------------------------------
# API: vouching
# ---------------------------------------------------------------------------

@app.route('/api/vouch/<int:vouchee_id>', methods=['POST'])
@login_required
def api_vouch(vouchee_id):
    vouchee = db.session.get(GoodNeighbor, vouchee_id)
    if not vouchee:
        return jsonify({'success': False, 'message': 'Neighbor not found.'}), 404
    if g.user.id == vouchee.id:
        return jsonify({'success': False, 'message': 'Cannot vouch for yourself.'}), 400
    if g.user.verification_tier < 2:
        return jsonify({'success': False, 'message': 'Only Tier 2 neighbors can vouch.'}), 403
    if vouchee.verification_tier >= 2:
        return jsonify({'success': False, 'message': 'Already In-Home Friendly.'}), 400
    if NeighborVouchPool.query.filter_by(voucher_id=g.user.id, vouchee_id=vouchee.id).first():
        return jsonify({'success': False, 'message': 'Already vouched.'}), 400

    # collusion guard: too many back-and-forth exchanges = can't vouch
    COLLUSION_THRESHOLD = 3
    mutual = HoursExchanged.query.filter(
        HoursExchanged.status == 'Released',
        db.or_(
            db.and_(HoursExchanged.payer_id == g.user.id, HoursExchanged.payee_id == vouchee.id),
            db.and_(HoursExchanged.payer_id == vouchee.id, HoursExchanged.payee_id == g.user.id)
        )
    ).count()

    if mutual >= COLLUSION_THRESHOLD:
        return jsonify({
            'success': False, 'collusion_flag': True,
            'message': (f"Heads up — you and @{vouchee.username} have exchanged {mutual} times. "
                        f"Frequent bilateral exchanges can't count toward vouching. "
                        f"Ask a different Tier 2 neighbor to vouch instead.")
        }), 400

    db.session.add(NeighborVouchPool(voucher_id=g.user.id, vouchee_id=vouchee.id))
    db.session.commit()

    count = db.session.query(NeighborVouchPool)\
        .join(GoodNeighbor, NeighborVouchPool.voucher_id == GoodNeighbor.id)\
        .filter(NeighborVouchPool.vouchee_id == vouchee.id, GoodNeighbor.verification_tier == 2).count()

    promoted = False
    if count >= 2:
        vouchee.verification_tier = 2
        db.session.commit()
        promoted = True

    return jsonify({
        'success': True, 'vouches_count': count, 'promoted': promoted,
        'message': (f'Vouch recorded! {count}/2 so far.' if not promoted
                    else 'Neighbor promoted to Tier 2: In-Home Friendly!')
    })


# ---------------------------------------------------------------------------
# API: reviews + flag/report
# ---------------------------------------------------------------------------

@app.route('/task/rate/<int:task_id>', methods=['POST'])
@app.route('/api/task/rate/<int:task_id>', methods=['POST'])
@login_required
def rate_task(task_id):
    is_api = request.path.startswith('/api/')
    svc = db.session.get(ActOfService, task_id)

    if not svc or svc.status != 'Completed':
        if is_api:
            return jsonify({'success': False, 'message': 'Cannot review this exchange.'}), 400
        flash('This exchange cannot be reviewed.', 'error')
        return redirect(url_for('tasks_hub'))

    if g.user.id not in [svc.provider_id, svc.receiver_id]:
        if is_api:
            return jsonify({'success': False, 'message': 'Not authorized.'}), 403
        flash('Not authorized to review this swap.', 'error')
        return redirect(url_for('tasks_hub'))

    if Review.query.filter_by(task_id=svc.id, reviewer_id=g.user.id).first():
        if is_api:
            return jsonify({'success': False, 'message': 'Already reviewed.'}), 400
        flash('You already shared feedback for this exchange.', 'error')
        return redirect(url_for('tasks_hub'))

    data = request.get_json(silent=True) or request.form
    rating = int(data.get('rating', 5))
    comment = (data.get('comment') or '').strip()
    endorsement_line = (data.get('endorsement_line') or '').strip()[:200]
    reviewee_id = svc.receiver_id if g.user.id == svc.provider_id else svc.provider_id

    review = Review(
        task_id=svc.id, reviewer_id=g.user.id, reviewee_id=reviewee_id,
        rating=rating, comment=comment, endorsement_line=endorsement_line or None,
        skill_category=svc.category
    )
    db.session.add(review)

    if rating >= 4:
        endorse = SkillEndorsement.query.filter_by(user_id=reviewee_id, skill_name=svc.category).first()
        if endorse:
            endorse.endorsement_count += 1
        else:
            db.session.add(SkillEndorsement(user_id=reviewee_id, skill_name=svc.category, endorsement_count=1))

    db.session.commit()

    if is_api:
        return jsonify({'success': True, 'message': "Your appreciation has been added to their neighborhood card!"})

    flash("Thank you! Your appreciation has been added to their neighborhood card.", 'success')
    return redirect(url_for('tasks_hub'))


@app.route('/api/report/<int:task_id>', methods=['POST'])
@login_required
def api_report_task(task_id):
    """Flag a listing as inappropriate. Two-step confirmation is handled on the frontend."""
    svc = db.session.get(ActOfService, task_id)
    if not svc:
        return jsonify({'success': False, 'message': 'Listing not found.'}), 404
    if svc.creator_id == g.user.id:
        return jsonify({'success': False, 'message': 'Cannot report your own listing.'}), 400

    existing = FlagReport.query.filter_by(task_id=task_id, reporter_id=g.user.id).first()
    if existing:
        return jsonify({'success': False, 'message': 'You have already reported this listing.'}), 400

    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or 'No reason provided').strip()[:200]

    db.session.add(FlagReport(task_id=task_id, reporter_id=g.user.id, reason=reason))
    db.session.commit()

    return jsonify({'success': True, 'message': 'Thank you for the heads up. Our team will review this listing.'})


# ---------------------------------------------------------------------------
# API: time bundle
# ---------------------------------------------------------------------------

@app.route('/api/bundle/contribute/<int:bundle_id>', methods=['POST'])
@login_required
def api_bundle_contribute(bundle_id):
    """
    Contribute 1 token to a community bundle.
    Concurrent safety: we use the unique constraint on (bundle_id, contributor_id)
    plus a re-read after insert to handle the race where two people hit this simultaneously.
    If the bundle tips over target after your contribution, you get refunded.
    """
    bundle = db.session.get(TimeBundle, bundle_id)
    if not bundle or bundle.status != 'Open':
        return jsonify({'success': False, 'message': 'This bundle is no longer open.'}), 400

    svc = db.session.get(ActOfService, bundle.task_id)
    if not svc or svc.creator_id == g.user.id:
        return jsonify({'success': False, 'message': 'Cannot contribute to your own bundle.'}), 400

    if g.user.time_tokens < 1:
        return jsonify({'success': False, 'message': "You don't have enough tokens to contribute right now."}), 400

    already = TimeBundleContribution.query.filter_by(
        bundle_id=bundle_id, contributor_id=g.user.id
    ).first()
    if already:
        return jsonify({'success': False, 'message': 'You have already contributed to this bundle.'}), 400

    # deduct token and record contribution
    g.user.time_tokens -= 1
    contrib = TimeBundleContribution(bundle_id=bundle_id, contributor_id=g.user.id, amount=1)
    db.session.add(contrib)

    try:
        db.session.flush()
    except Exception:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Contribution conflict — please try again.'}), 409

    # re-count in case of concurrent submissions
    total_contrib = db.session.query(db.func.sum(TimeBundleContribution.amount))\
        .filter(TimeBundleContribution.bundle_id == bundle_id).scalar() or 0
    bundle.collected_tokens = total_contrib

    completed = False
    if total_contrib >= bundle.target_tokens:
        bundle.status = 'Completed'
        svc.status = 'Accepted'
        completed = True

    db.session.commit()

    return jsonify({
        'success': True,
        'completed': completed,
        'collected': bundle.collected_tokens,
        'target': bundle.target_tokens,
        'user_tokens': g.user.time_tokens,
        'message': ('The bundle is fully funded! The swap is now active.' if completed
                    else f"{bundle.collected_tokens}/{bundle.target_tokens} tokens collected — keep spreading the word!")
    })


@app.route('/api/bundle/<int:task_id>')
@login_required
def api_get_bundle(task_id):
    svc = db.session.get(ActOfService, task_id)
    if not svc or not svc.bundle:
        return jsonify({'success': False, 'message': 'No bundle for this task.'}), 404
    return jsonify({'success': True, 'bundle': svc.bundle.to_dict()})


# ---------------------------------------------------------------------------
# API: admin
# ---------------------------------------------------------------------------

@app.route('/api/admin/toggle-tier/<int:user_id>', methods=['POST'])
@login_required
def api_admin_toggle_tier(user_id):
    nbr = db.session.get(GoodNeighbor, user_id)
    if not nbr:
        return jsonify({'success': False, 'message': 'Not found.'}), 404

    if nbr.verification_tier == 1:
        nbr.verification_tier = 2
        msg = 'Promoted to Tier 2: In-Home Friendly.'
    else:
        nbr.verification_tier = 1
        NeighborVouchPool.query.filter_by(vouchee_id=nbr.id).delete()
        msg = 'Returned to Tier 1: Digital/Public Help Only.'

    db.session.commit()
    return jsonify({'success': True, 'new_tier': nbr.verification_tier, 'message': msg})


@app.route('/api/admin/fraud-logs')
@login_required
def api_admin_fraud_logs():
    """Shows unresolved fraud flags — for judges/admin review."""
    logs = FraudLog.query.filter_by(resolved=False).order_by(FraudLog.created_at.desc()).limit(50).all()
    result = []
    for log in logs:
        result.append({
            'id': log.id,
            'user_id': log.user_id,
            'username': log.user.username,
            'flag_type': log.flag_type,
            'details': log.details,
            'created_at': log.created_at.isoformat()
        })
    return jsonify({'success': True, 'flags': result})


if __name__ == '__main__':
    import os as _os
    debug_mode = _os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(_os.environ.get('PORT', 5001)))
