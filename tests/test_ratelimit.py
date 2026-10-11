import pytest

from app.ratelimit import RateLimiter, Rule


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


def ip_rule(limit=2, window=60, name="r", message="slow down"):
    return Rule(name, "ip", limit, window, message)


def test_allows_up_to_the_limit_then_refuses(clock):
    limiter = RateLimiter([ip_rule(limit=2)], clock=clock)
    assert limiter.check("1.1.1.1").allowed
    assert limiter.check("1.1.1.1").allowed
    clock.t = 10
    d = limiter.check("1.1.1.1")
    assert not d.allowed
    assert d.message == "slow down"
    assert d.retry_after == 50  # the oldest hit was at t=0 and the window is 60 s


def test_window_slides(clock):
    limiter = RateLimiter([ip_rule(limit=2)], clock=clock)
    limiter.check("a")
    limiter.check("a")
    assert not limiter.check("a").allowed
    clock.t = 61
    assert limiter.check("a").allowed


def test_refused_requests_are_not_counted(clock):
    limiter = RateLimiter([ip_rule(limit=1)], clock=clock)
    assert limiter.check("a").allowed          # recorded at t=0
    for t in (1, 2, 3, 30):
        clock.t = t
        assert not limiter.check("a").allowed  # must not extend the block
    clock.t = 60
    assert limiter.check("a").allowed


def test_each_ip_has_its_own_counter(clock):
    limiter = RateLimiter([ip_rule(limit=1)], clock=clock)
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    assert limiter.check("b").allowed


def test_global_rule_is_shared_by_everyone(clock):
    limiter = RateLimiter([Rule("g", "global", 1, 60, "busy")], clock=clock)
    assert limiter.check("a").allowed
    d = limiter.check("b")
    assert not d.allowed and d.message == "busy"


def test_the_longest_wait_decides_the_message(clock):
    limiter = RateLimiter([
        Rule("ip", "ip", 1, 600, "ip limit"),
        Rule("g", "global", 1, 60, "global limit"),
    ], clock=clock)
    assert limiter.check("a").allowed
    clock.t = 10
    d = limiter.check("a")
    assert not d.allowed
    assert d.message == "ip limit"
    assert d.retry_after == 590


def test_a_global_refusal_is_reported_when_it_is_the_only_one(clock):
    limiter = RateLimiter([
        Rule("ip", "ip", 2, 600, "ip limit"),
        Rule("g", "global", 1, 60, "global limit"),
    ], clock=clock)
    assert limiter.check("a").allowed
    clock.t = 10
    d = limiter.check("a")
    assert not d.allowed and d.message == "global limit" and d.retry_after == 50


def test_a_refusal_by_one_rule_records_nothing_in_the_others(clock):
    limiter = RateLimiter([
        Rule("ip", "ip", 1, 60, "ip limit"),
        Rule("g", "global", 2, 60, "global limit"),
    ], clock=clock)
    assert limiter.check("a").allowed         # global count 1
    assert not limiter.check("a").allowed     # refused by the ip rule: global must stay at 1
    assert limiter.check("b").allowed         # global count 2
    assert not limiter.check("c").allowed     # now the global rule refuses


def test_retry_after_is_at_least_one_second(clock):
    limiter = RateLimiter([ip_rule(limit=1)], clock=clock)
    limiter.check("a")
    clock.t = 59.5
    d = limiter.check("a")
    assert not d.allowed and d.retry_after == 1


def test_disabled_limiter_allows_everything(clock):
    limiter = RateLimiter([ip_rule(limit=1)], clock=clock)
    limiter.enabled = False
    assert all(limiter.check("a").allowed for _ in range(5))


def test_oldest_addresses_are_forgotten_when_max_keys_is_reached(clock):
    limiter = RateLimiter([ip_rule(limit=1)], clock=clock, max_keys=2)
    for ip in ("a", "b", "c"):
        assert limiter.check(ip).allowed
    assert not limiter.check("c").allowed   # still remembered
    assert limiter.check("a").allowed       # forgotten, so it starts fresh


def test_allowed_decision_has_no_message_and_no_wait(clock):
    d = RateLimiter([ip_rule()], clock=clock).check("a")
    assert d.allowed and d.retry_after == 0 and d.message == ""