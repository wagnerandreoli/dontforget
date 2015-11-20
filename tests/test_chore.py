# -*- coding: utf-8 -*-
# pylint: disable=invalid-name,no-member
"""Test chores."""
from datetime import datetime, timedelta

from dontforget.cron import spawn_alarms
from dontforget.models import AlarmState, Chore
from tests.factories import ChoreFactory

TODAY = datetime.now()
NEXT_WEEK = TODAY + timedelta(days=7)
YESTERDAY = TODAY - timedelta(days=1)


def test_search_similar(db):
    """Search for similar chores."""
    first = ChoreFactory(title='My first chore')
    something = ChoreFactory(title='Do SOMETHING soon')
    coffee = ChoreFactory(title='Buy coffee')
    cheese = ChoreFactory(title='Buy cheese')
    db.session.commit()
    assert len(Chore(title='Write anything').search_similar()) == 0

    rv = Chore(title='Read something now').search_similar()
    assert len(rv) == 1
    assert rv == [something]

    rv = Chore(title='Buy bread').search_similar()
    assert len(rv) == 2
    assert {coffee, cheese}.issubset(set(rv))

    assert len(Chore(title='Buy bread').search_similar(min_chars=4)) == 0
    assert len(Chore(title='My duty').search_similar()) == 0

    rv = Chore(title='My first duty').search_similar()
    assert len(rv) == 1
    assert rv == [first]


def test_create_alarms_for_active_chores(db):
    """Create alarms for active chores."""
    veggie = ChoreFactory(title='Buy vegetables', alarm_start=YESTERDAY, alarm_end=YESTERDAY)
    coffee = ChoreFactory(title='Buy coffee', alarm_start=YESTERDAY, alarm_end=NEXT_WEEK)
    chocolate = ChoreFactory(title='Buy chocolate', alarm_start=YESTERDAY)
    db.session.commit()

    assert spawn_alarms(TODAY) == 2
    db.session.commit()

    # No alarms for inactive chores, one alarm each for each active chore.
    assert len(veggie.alarms) == 0

    assert len(coffee.alarms) == 1
    alarm = coffee.alarms[0]
    assert alarm.next_at == coffee.alarm_start
    assert alarm.current_state == AlarmState.UNSEEN

    assert len(chocolate.alarms) == 1
    alarm = chocolate.alarms[0]
    assert alarm.next_at == chocolate.alarm_start
    assert alarm.current_state == AlarmState.UNSEEN

    # There should be one new alarm for chocolate.
    assert len(veggie.alarms) == 0
    assert len(coffee.alarms) == 1
    assert len(chocolate.alarms) == 1
    assert chocolate.alarms[0].next_at == chocolate.alarm_start
    assert chocolate.alarms[0].current_state == AlarmState.UNSEEN

    # Nothing changed, so no spawn for you.
    assert spawn_alarms(TODAY) == 0


def test_one_time_only_chore(db):
    """Create chore without repetition and open end."""
    chore = ChoreFactory(title='Buy house', repetition=None, alarm_start=YESTERDAY)
    db.session.commit()

    # Spawn one alarm.
    assert spawn_alarms() == 1

    # Mark as done.
    chore.alarms[0].current_state = AlarmState.COMPLETED
    chore.alarms[0].save()

    # No alarm should be spawned.
    assert spawn_alarms() == 0


def test_daily_chore(db):
    """Create chore with daily repetition and open end."""
    chore = ChoreFactory(title='Drink coffee', repetition='Daily', alarm_start=YESTERDAY)
    db.session.commit()

    # Spawn one alarm.
    assert spawn_alarms() == 1
    assert chore.alarms[0].current_state == AlarmState.UNSEEN
    assert chore.alarms[0].next_at == chore.alarm_start

    # Mark as done.
    chore.alarms[0].current_state = AlarmState.COMPLETED
    chore.alarms[0].save()

    # Spawn one alarm for the next day.
    assert spawn_alarms() == 1
    assert chore.alarms[1].current_state == AlarmState.UNSEEN
    assert chore.alarms[1].next_at == chore.alarm_start + timedelta(days=1)

    # Mark as done again.
    chore.alarms[1].current_state = AlarmState.COMPLETED
    chore.alarms[1].save()

    # Spawn one alarm for the next day.
    assert spawn_alarms() == 1
    assert chore.alarms[2].current_state == AlarmState.UNSEEN
    assert chore.alarms[2].next_at == chore.alarms[1].next_at + timedelta(days=1)

    # Kill the chore.
    chore.alarms[2].current_state = AlarmState.KILLED
    chore.alarms[2].save()

    # No alarm should be spawned.
    assert spawn_alarms() == 0
