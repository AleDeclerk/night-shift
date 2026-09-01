import datetime as dt

from nightshift import backends, cascade, db

# Same week as tests/test_quota.py, one calendar day per test.
MON = dt.datetime(2026, 8, 24, 9, 0)
TUE = dt.datetime(2026, 8, 25, 9, 0)
WED = dt.datetime(2026, 8, 26, 9, 0)
THU = dt.datetime(2026, 8, 27, 9, 0)
FRI = dt.datetime(2026, 8, 28, 9, 0)
SAT = dt.datetime(2026, 8, 29, 9, 0)
SUN = dt.datetime(2026, 8, 30, 9, 0)


def _engine(name, installed=True, signed_in=True):
    """A fixed backends.Engine, so no test ever shells out for real."""
    return backends.Engine(name, name.upper(), "test", installed, signed_in,
                           False, False, False, False)


ALL_ON = [_engine("claude"), _engine("cursor"), _engine("ollama")]


def _event(conn, engine, cost_usd=0.0, at=None):
    conn.execute(
        "INSERT INTO events (at, kind, engine, cost_usd) VALUES (?,?,?,?)",
        ((at or dt.datetime.now()).isoformat(), "draft_written", engine,
         cost_usd))
    conn.commit()


# --- reserve_for: the table of section 4, day by day, ceiling 20 -----------

def test_reserve_table_monday():
    assert cascade.reserve_for(MON, 20.0) == 12.0


def test_reserve_table_tuesday():
    assert cascade.reserve_for(TUE, 20.0) == 10.0


def test_reserve_table_wednesday():
    assert cascade.reserve_for(WED, 20.0) == 8.0


def test_reserve_table_thursday():
    assert cascade.reserve_for(THU, 20.0) == 6.0


def test_reserve_table_friday():
    assert cascade.reserve_for(FRI, 20.0) == 4.0


def test_reserve_table_saturday():
    assert cascade.reserve_for(SAT, 20.0) == 2.0


def test_reserve_table_sunday():
    """Given Sunday and a ceiling of 20, the system may use all 20."""
    assert cascade.reserve_for(SUN, 20.0) == 0.0


# --- spent_by: claude sums cost, cursor counts calls, local gives 0 --------

def test_spent_by_claude_sums_cost(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=1.5, at=WED)
    _event(conn, "claude", cost_usd=2.25, at=WED)
    _event(conn, "cursor", cost_usd=0.0, at=WED)  # must not leak in
    assert cascade.spent_by(conn, "claude", WED) == 3.75


def test_spent_by_cursor_counts_calls_not_cost(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for _ in range(5):
        _event(conn, "cursor", cost_usd=0.0, at=WED)
    assert cascade.spent_by(conn, "cursor", WED) == 5.0


def test_spent_by_local_is_always_zero(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "ollama", cost_usd=99.0, at=WED)  # even if something wrote a cost
    assert cascade.spent_by(conn, "ollama", WED) == 0.0


def test_spent_by_ignores_last_week(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=9.0, at=MON - dt.timedelta(days=1))
    assert cascade.spent_by(conn, "claude", WED) == 0.0


# --- has_room -----------------------------------------------------------

def test_the_local_engine_always_has_room(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for _ in range(1000):
        _event(conn, "ollama", cost_usd=0.0, at=SUN)
    ok, _reason = cascade.has_room(conn, cascade.LADDER[-1], SUN)
    assert ok is True


def test_claude_under_its_allowance_has_room(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=1.0, at=WED)
    ok, _reason = cascade.has_room(conn, cascade.LADDER[0], WED)
    assert ok is True


def test_claude_over_its_allowance_today_has_no_room(tmp_path):
    """Wednesday's reserve is 8 of a ceiling of 20, so the system may use 12."""
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=12.0, at=WED)
    ok, reason = cascade.has_room(conn, cascade.LADDER[0], WED)
    assert ok is False
    assert "claude" in reason


def test_cursor_over_its_call_ceiling_has_no_room(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    for _ in range(60):
        _event(conn, "cursor", cost_usd=0.0, at=WED)
    ok, _reason = cascade.has_room(conn, cascade.LADDER[1], WED)  # grok
    assert ok is False


def test_grok_and_flash_share_the_cursor_count(tmp_path):
    """Spending on grok moves flash's own room too: both read the same
    cursor call count, because they share one subscription."""
    conn = db.connect(tmp_path / "s.db")
    for _ in range(60):
        _event(conn, "cursor", cost_usd=0.0, at=WED)
    grok_ok, _ = cascade.has_room(conn, cascade.LADDER[1], WED)
    flash_ok, _ = cascade.has_room(conn, cascade.LADDER[2], WED)
    assert grok_ok is False
    assert flash_ok is False


# --- choose: acceptance criteria of section 8 --------------------------

def test_claude_under_its_allowance_runs_on_claude(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    step, skipped = cascade.choose(conn, WED, probes={}, engines=ALL_ON)
    assert step.name == "claude"
    assert skipped == []


def test_claude_over_its_allowance_falls_to_grok(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=15.0, at=WED)  # over the 12 Wednesday allows
    step, skipped = cascade.choose(conn, WED, probes={}, engines=ALL_ON)
    assert step.name == "grok"
    assert any("claude" in s for s in skipped)


def test_cursor_over_its_ceiling_falls_to_the_local_engine(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=15.0, at=WED)   # claude out of room
    for _ in range(60):
        _event(conn, "cursor", cost_usd=0.0, at=WED)  # grok and flash too
    step, skipped = cascade.choose(conn, WED, probes={}, engines=ALL_ON)
    assert step.name == "local"
    assert len(skipped) == 3   # claude, grok, flash


def test_a_failed_probe_skips_its_step(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    probes = {"claude": {"ok": False}}
    step, skipped = cascade.choose(conn, WED, probes=probes, engines=ALL_ON)
    assert step.name == "grok"
    assert any("claude" in s and "probe" in s for s in skipped)


def test_an_engine_with_no_session_is_skipped(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    engines_ = [_engine("claude", signed_in=False), _engine("cursor"),
               _engine("ollama")]
    step, skipped = cascade.choose(conn, WED, probes={}, engines=engines_)
    assert step.name == "grok"
    assert any("claude" in s for s in skipped)


def test_a_forced_engine_skips_the_walk(tmp_path):
    """The user forced a step by hand: the ladder is not walked at all, even
    though claude has no room."""
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=999.0, at=WED)
    step, skipped = cascade.choose(conn, WED, forced="local", probes={},
                                   engines=[])
    assert step.name == "local"
    assert skipped == []


def test_choose_reports_each_skipped_step_with_a_reason(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=15.0, at=WED)
    for _ in range(60):
        _event(conn, "cursor", cost_usd=0.0, at=WED)
    _step, skipped = cascade.choose(conn, WED, probes={}, engines=ALL_ON)
    assert len(skipped) == 3
    for line in skipped:
        assert ":" in line   # "<step name>: <reason>"


# --- one ceiling, read where the user sets it ---------------------------

def test_the_environment_moves_what_the_cascade_allows(tmp_path, monkeypatch):
    """The first step of the ladder held a third copy of 20.0. A user who
    raised NIGHTSHIFT_CEILING_USD moved the cycle and the tick, and the
    cascade went on judging against 20."""
    conn = db.connect(tmp_path / "s.db")
    _event(conn, "claude", cost_usd=3.0, at=SUN)   # Sunday holds back nothing

    monkeypatch.setenv("NIGHTSHIFT_CEILING_USD", "2.0")
    assert cascade.has_room(conn, cascade.LADDER[0], SUN)[0] is False

    monkeypatch.setenv("NIGHTSHIFT_CEILING_USD", "45.0")
    assert cascade.has_room(conn, cascade.LADDER[0], SUN)[0] is True
