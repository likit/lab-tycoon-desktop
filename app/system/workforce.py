"""Scenario-local staff identities, independent of LIS accounts."""
from uuid import uuid4, uuid5, NAMESPACE_URL


def validate_workforce(workforce):
    result = []
    ids, names = set(), set()
    for member in workforce:
        staff_id = str(member.get('id', '')).strip()
        name = str(member.get('name', '')).strip()
        if not staff_id or staff_id in ids:
            raise ValueError('Each simulated staff member needs a unique ID.')
        if not name or name.casefold() in names:
            raise ValueError('Each simulated staff member needs a unique, non-empty name.')
        ids.add(staff_id)
        names.add(name.casefold())
        result.append(dict(id=staff_id, name=name))
    if not result:
        raise ValueError('Add at least one simulated staff member.')
    return result


def new_staff(name):
    return dict(id=uuid4().hex, name=name)


def scenario_workforce(scenario, capacity):
    """Expose legacy headcounts as deterministic names without mutating storage."""
    if scenario.workforce:
        return validate_workforce(scenario.workforce)
    return [dict(id=uuid5(NAMESPACE_URL, f'{scenario.scenario_id}:staff:{i}').hex,
                 name=f'Staff {i}') for i in range(1, capacity + 1)]


def eligible_staff(roster, assignments, node_id):
    """Missing/None means All; an explicit empty list means nobody."""
    selected = assignments.get(node_id)
    if selected is None:
        return list(roster)
    known = {member['id'] for member in roster}
    if set(selected) - known:
        raise ValueError('Allowed staff includes a removed staff member. Update the node assignment.')
    return [member for member in roster if member['id'] in selected]


class WorkforcePool:
    """Reserve entire eligible teams atomically, in oldest-satisfiable order."""

    def __init__(self, env, roster):
        self.env = env
        self.roster = validate_workforce(roster)
        self.free = {member['id'] for member in self.roster}
        self.pending = []

    @property
    def level(self):
        return len(self.free)

    def get(self, count, eligible):
        event = self.env.event()
        self.pending.append((event, count, {member['id'] for member in eligible}))
        self._dispatch()
        return event

    def put(self, members):
        self.free.update(member['id'] for member in members)
        self._dispatch()
        return self.env.event().succeed()

    def _dispatch(self):
        for request in list(self.pending):
            event, count, eligible = request
            available = [member for member in self.roster
                         if member['id'] in self.free and member['id'] in eligible]
            if len(available) >= count:
                team = available[:count]
                self.free.difference_update(member['id'] for member in team)
                self.pending.remove(request)
                event.succeed(team)
