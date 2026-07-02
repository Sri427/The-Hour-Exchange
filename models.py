import datetime
import hashlib
import os
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class GoodNeighbor(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    time_tokens = db.Column(db.Integer, default=10)
    verification_tier = db.Column(db.Integer, default=1)
    completed_tasks_count = db.Column(db.Integer, default=0)
    late_cancellations_count = db.Column(db.Integer, default=0)
    # tracks how many times someone never confirmed a swap — drags down reliability
    ghost_confirmation_count = db.Column(db.Integer, default=0)
    email_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    vouches_received = db.relationship('NeighborVouchPool', foreign_keys='NeighborVouchPool.vouchee_id', backref='vouchee', lazy='dynamic')
    vouches_given = db.relationship('NeighborVouchPool', foreign_keys='NeighborVouchPool.voucher_id', backref='voucher', lazy='dynamic')

    @property
    def reliability_score(self):
        """
        Weighted rolling reliability formula:

        base = completed / (completed + late_cancels) * 100
        ghost_penalty = ghost_count * 5 (max 30 pts subtracted)
        endorsement_boost = 1.0 + (0.02 * total_endorsements), capped at 1.1x

        final = (base - ghost_penalty) * endorsement_boost, clamped 0-100

        Why weighted: a neighbor with 10 completions and 1 late cancel is way
        more trustworthy than someone with 1 completion and 0 late cancels.
        Endorsements give a small reward for being genuinely good at your skill.
        """
        total = self.completed_tasks_count + self.late_cancellations_count
        if total == 0:
            return 100.0

        base = (self.completed_tasks_count / total) * 100.0

        ghost_pen = min(self.ghost_confirmation_count * 5, 30)

        total_endorsements = sum(e.endorsement_count for e in self.endorsements) if self.endorsements else 0
        boost = min(1.0 + 0.02 * total_endorsements, 1.1)

        score = (base - ghost_pen) * boost
        return round(max(0.0, min(100.0, score)), 1)

    @property
    def reliability_rating(self):
        return self.reliability_score

    @property
    def badge(self):
        """returns (label, icon_name) based on activity thresholds"""
        c = self.completed_tasks_count
        late_ratio = self.late_cancellations_count / max(c + self.late_cancellations_count, 1)

        if c >= 10 and late_ratio < 0.1:
            return ('Community Star', 'star')
        elif c >= 5:
            return ('Trusted Neighbor', 'shield-check')
        elif c >= 1:
            return ('Active Neighbor', 'handshake')
        else:
            return ('New Member', 'seedling')

    def set_password(self, password):
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        self.password_hash = salt.hex() + '$' + key.hex()

    def check_password(self, password):
        try:
            salt_hex, key_hex = self.password_hash.split('$')
            salt = bytes.fromhex(salt_hex)
            key = bytes.fromhex(key_hex)
            new_key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
            return new_key == key
        except Exception:
            return False

    def to_dict(self):
        badge_label, badge_icon = self.badge
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'hour_tokens': self.time_tokens,
            'verification_tier': self.verification_tier,
            'completed_tasks_count': self.completed_tasks_count,
            'late_cancellations_count': self.late_cancellations_count,
            'reliability_score': self.reliability_score,
            'badge_label': badge_label,
            'badge_icon': badge_icon,
            'email_verified': self.email_verified
        }


class NeighborVouchPool(db.Model):
    __tablename__ = 'vouches'

    id = db.Column(db.Integer, primary_key=True)
    voucher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    vouchee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('voucher_id', 'vouchee_id', name='_voucher_vouchee_uc'),)


class ActOfService(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    task_type = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    urgency = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Open')
    tokens_value = db.Column(db.Integer, default=1)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    provider_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    # is_bundle: if True, this task has a community pooling record
    is_bundle = db.Column(db.Boolean, default=False)

    creator = db.relationship('GoodNeighbor', foreign_keys=[creator_id], backref='created_tasks')
    provider = db.relationship('GoodNeighbor', foreign_keys=[provider_id], backref='provided_tasks')
    receiver = db.relationship('GoodNeighbor', foreign_keys=[receiver_id], backref='received_tasks')
    escrow = db.relationship('HoursExchanged', backref='task', uselist=False, cascade="all, delete-orphan")
    bundle = db.relationship('TimeBundle', backref='task', uselist=False, cascade="all, delete-orphan")
    flag_reports = db.relationship('FlagReport', backref='task', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'task_type': self.task_type.lower() if self.task_type else None,
            'category': self.category,
            'urgency': self.urgency.lower() if self.urgency else None,
            'status': self.status,
            'tokens_value': self.tokens_value,
            'is_bundle': self.is_bundle,
            'creator_id': self.creator_id,
            'creator_username': self.creator.username,
            'creator_tier': self.creator.verification_tier,
            'creator_badge': self.creator.badge[0],
            'provider_id': self.provider_id,
            'provider_username': self.provider.username if self.provider else None,
            'receiver_id': self.receiver_id,
            'receiver_username': self.receiver.username if self.receiver else None,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'lat': self.lat,
            'lng': self.lng
        }


class HoursExchanged(db.Model):
    """Holds tokens in escrow while a swap is in progress."""
    __tablename__ = 'escrow_transactions'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    payer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    payee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Locked')
    payer_confirmed = db.Column(db.Boolean, default=False)
    payee_confirmed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    deadline = db.Column(db.DateTime, nullable=True)

    payer = db.relationship('GoodNeighbor', foreign_keys=[payer_id], backref='escrow_payments')
    payee = db.relationship('GoodNeighbor', foreign_keys=[payee_id], backref='escrow_receipts')

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'payer_id': self.payer_id,
            'payer_username': self.payer.username,
            'payee_id': self.payee_id,
            'payee_username': self.payee.username,
            'amount': self.amount,
            'status': self.status,
            'payer_confirmed': self.payer_confirmed,
            'payee_confirmed': self.payee_confirmed,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class Review(db.Model):
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reviewee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    # one-line skill endorsement shown on profile
    endorsement_line = db.Column(db.String(200), nullable=True)
    skill_category = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    reviewer = db.relationship('GoodNeighbor', foreign_keys=[reviewer_id], backref='reviews_written')
    reviewee = db.relationship('GoodNeighbor', foreign_keys=[reviewee_id], backref='reviews_received')

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'reviewer_id': self.reviewer_id,
            'reviewer_username': self.reviewer.username,
            'reviewee_id': self.reviewee_id,
            'rating': self.rating,
            'comment': self.comment,
            'endorsement_line': self.endorsement_line,
            'skill_category': self.skill_category,
            'created_at': self.created_at.isoformat()
        }


class SkillEndorsement(db.Model):
    __tablename__ = 'skill_endorsements'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    skill_name = db.Column(db.String(50), nullable=False)
    endorsement_count = db.Column(db.Integer, default=0)

    user = db.relationship('GoodNeighbor', backref='endorsements')

    __table_args__ = (db.UniqueConstraint('user_id', 'skill_name', name='_user_skill_uc'),)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'skill_name': self.skill_name,
            'endorsement_count': self.endorsement_count
        }


class TimeBundle(db.Model):
    """
    Community pooling — lets multiple neighbors each chip in 1 token
    toward a bigger task that needs more hours than one person can spare.
    """
    __tablename__ = 'time_bundles'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    target_tokens = db.Column(db.Integer, nullable=False)
    collected_tokens = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='Open')  # Open / Completed / Cancelled
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    contributions = db.relationship('TimeBundleContribution', backref='bundle', lazy='dynamic', cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'target_tokens': self.target_tokens,
            'collected_tokens': self.collected_tokens,
            'status': self.status,
            'contributors': [c.contributor_id for c in self.contributions]
        }


class TimeBundleContribution(db.Model):
    __tablename__ = 'bundle_contributions'

    id = db.Column(db.Integer, primary_key=True)
    bundle_id = db.Column(db.Integer, db.ForeignKey('time_bundles.id'), nullable=False)
    contributor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    contributor = db.relationship('GoodNeighbor', backref='bundle_contributions')

    # unique constraint: one contribution per person per bundle
    __table_args__ = (db.UniqueConstraint('bundle_id', 'contributor_id', name='_bundle_contrib_uc'),)


class FraudLog(db.Model):
    """Admin-visible log of suspicious exchange patterns."""
    __tablename__ = 'fraud_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    flag_type = db.Column(db.String(50), nullable=False)  # velocity / new_account / single_party
    details = db.Column(db.Text, nullable=True)
    resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    user = db.relationship('GoodNeighbor', backref='fraud_flags')


class FlagReport(db.Model):
    """A report filed against a listing by a neighbor."""
    __tablename__ = 'flag_reports'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    reporter = db.relationship('GoodNeighbor', backref='reports_filed')

    # one report per person per listing
    __table_args__ = (db.UniqueConstraint('task_id', 'reporter_id', name='_flag_report_uc'),)


# testing aliases — keep these at the bottom
User = GoodNeighbor
Task = ActOfService
Vouch = NeighborVouchPool
EscrowTransaction = HoursExchanged
