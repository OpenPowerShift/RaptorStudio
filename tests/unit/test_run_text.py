"""`run_outcome_text`: the status line for how a batch ended."""
from raptor.gui.run_text import run_outcome_text


def test_a_finished_batch_says_it_succeeded():
    assert run_outcome_text(stopped=False, completed=3, logs=3, event_log_lines=40) \
        == ("Completed 3 run(s) successfully, 40 event-log lines", 0)


def test_a_stop_between_runs_reports_how_many_finished():
    assert run_outcome_text(stopped=True, completed=2, logs=2, event_log_lines=9) \
        == ("Stopped after 2 run(s), 9 event-log lines", 0)


def test_a_stop_mid_run_says_the_partial_log_was_kept_and_lingers():
    text, timeout = run_outcome_text(stopped=True, completed=2, logs=3, event_log_lines=9)
    assert text.startswith("Stopped — partial event log kept") and timeout == 10000
