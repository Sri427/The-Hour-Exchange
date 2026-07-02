import datetime
from models import db, GoodNeighbor, ActOfService, HoursExchanged, Review, SkillEndorsement, NeighborVouchPool


def init_db(app):
    with app.app_context():
        db.create_all()
        seed_data()


def seed_data():
    if GoodNeighbor.query.first() is not None:
        return

    # alice is the main demo account — tier 2, has the most history
    alice = GoodNeighbor(username="alice", email="alice@hour-exchange.org",
                         time_tokens=15, verification_tier=2,
                         completed_tasks_count=5, late_cancellations_count=0,
                         email_verified=True)
    alice.set_password("password123")

    bob = GoodNeighbor(username="bob", email="bob@hour-exchange.org",
                       time_tokens=12, verification_tier=2,
                       completed_tasks_count=3, late_cancellations_count=0,
                       email_verified=True)
    bob.set_password("password123")

    charlie = GoodNeighbor(username="charlie", email="charlie@hour-exchange.org",
                           time_tokens=10, verification_tier=1,
                           completed_tasks_count=0, late_cancellations_count=0,
                           email_verified=True)
    charlie.set_password("password123")

    diana = GoodNeighbor(username="diana", email="diana@hour-exchange.org",
                         time_tokens=8, verification_tier=1,
                         completed_tasks_count=1, late_cancellations_count=0,
                         email_verified=True)
    diana.set_password("password123")

    # elena has a bunch of completed swaps, good for showing off the reliability score
    elena = GoodNeighbor(username="elena", email="elena@hour-exchange.org",
                         time_tokens=20, verification_tier=2,
                         completed_tasks_count=8, late_cancellations_count=1,
                         email_verified=True)
    elena.set_password("password123")

    mrs_gable = GoodNeighbor(username="mrs_gable", email="gable@hour-exchange.org",
                             time_tokens=10, verification_tier=1,
                             completed_tasks_count=0, late_cancellations_count=0,
                             email_verified=True)
    mrs_gable.set_password("password123")

    marcus = GoodNeighbor(username="marcus", email="marcus@hour-exchange.org",
                          time_tokens=10, verification_tier=1,
                          completed_tasks_count=0, late_cancellations_count=0,
                          email_verified=True)
    marcus.set_password("password123")

    maya = GoodNeighbor(username="maya", email="maya@hour-exchange.org",
                        time_tokens=10, verification_tier=1,
                        completed_tasks_count=0, late_cancellations_count=0,
                        email_verified=True)
    maya.set_password("password123")

    db.session.add_all([alice, bob, charlie, diana, elena, mrs_gable, marcus, maya])
    db.session.commit()

    # alice already vouched for bob before the demo starts
    db.session.add(NeighborVouchPool(voucher_id=alice.id, vouchee_id=bob.id))

    db.session.add_all([
        SkillEndorsement(user_id=alice.id, skill_name="Learning & Growing", endorsement_count=4),
        SkillEndorsement(user_id=alice.id, skill_name="Tech Help", endorsement_count=2),
        SkillEndorsement(user_id=bob.id, skill_name="Lending a Hand", endorsement_count=3),
        SkillEndorsement(user_id=elena.id, skill_name="Everyday Errands", endorsement_count=5),
        SkillEndorsement(user_id=diana.id, skill_name="Creative Arts", endorsement_count=1)
    ])
    db.session.commit()

    # the 3 main demo requests that show up on the board
    t1 = ActOfService(
        title="Yard Clearing & Gutter Cleaning before the Rains",
        description="Mrs. Gable (Elderly Neighbor) needs 2 hours of yard clearing/gutter cleaning before the rains.",
        task_type="REQUEST", category="Lending a Hand", urgency="High",
        status="Open", tokens_value=2,
        creator_id=mrs_gable.id, receiver_id=mrs_gable.id,
        scheduled_at=datetime.datetime.utcnow() + datetime.timedelta(days=2),
        lat=250.0, lng=300.0
    )

    t2 = ActOfService(
        title="Algebra or Pre-Calculus Final Prep",
        description="Marcus (High School Junior) needs 1 hour of algebra or pre-calculus tutoring before his finals.",
        task_type="REQUEST", category="Learning & Growing", urgency="High",
        status="Open", tokens_value=1,
        creator_id=marcus.id, receiver_id=marcus.id,
        scheduled_at=datetime.datetime.utcnow() + datetime.timedelta(days=1),
        lat=480.0, lng=200.0
    )

    t3 = ActOfService(
        title="Moving Heavy Storage Bins to Attic",
        description="Maya (New Parent) needs a 2-hour hand moving heavy storage bins to her attic.",
        task_type="REQUEST", category="Lending a Hand", urgency="Medium",
        status="Open", tokens_value=2,
        creator_id=maya.id, receiver_id=maya.id,
        scheduled_at=datetime.datetime.utcnow() + datetime.timedelta(days=3),
        lat=320.0, lng=380.0
    )

    # alice's skill offer to balance the board
    t4 = ActOfService(
        title="Helpful Python & Flask Mentorship Hour",
        description="Happy to spend an hour explaining database models, styling layout templates, or fixing syntax exceptions.",
        task_type="OFFER", category="Tech Help", urgency="Medium",
        status="Open", tokens_value=1,
        creator_id=alice.id, provider_id=alice.id,
        scheduled_at=datetime.datetime.utcnow() + datetime.timedelta(days=5),
        lat=550.0, lng=150.0
    )

    db.session.add_all([t1, t2, t3, t4])
    db.session.commit()

    # one completed exchange so the global counter isn't zero on load
    t5 = ActOfService(
        title="Watercolor Pencil Sketching Guidance",
        description="Spent 2 hours showing drawing layout techniques and pencil shading.",
        task_type="REQUEST", category="Creative Arts", urgency="Low",
        status="Completed", tokens_value=2,
        creator_id=diana.id, receiver_id=diana.id, provider_id=alice.id,
        scheduled_at=datetime.datetime.utcnow() - datetime.timedelta(days=4),
        lat=650.0, lng=220.0
    )
    db.session.add(t5)
    db.session.commit()

    escrow = HoursExchanged(
        task_id=t5.id, payer_id=diana.id, payee_id=alice.id,
        amount=2, status="Released",
        payer_confirmed=True, payee_confirmed=True
    )
    db.session.add(escrow)
    db.session.commit()

    r1 = Review(task_id=t5.id, reviewer_id=diana.id, reviewee_id=alice.id, rating=5,
                comment="Alice is an amazing sketching teacher! I highly recommend her.",
                skill_category="Creative Arts")
    r2 = Review(task_id=t5.id, reviewer_id=alice.id, reviewee_id=diana.id, rating=5,
                comment="Diana is a focused student and a natural creative.",
                skill_category="Creative Arts")
    db.session.add_all([r1, r2])
    db.session.commit()
