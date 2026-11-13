"""The cut list the demo renderer builds from a recording's timeline.

A gap or an overlap here would drop or repeat seconds of the video, and the
result would still be a playable file that looks fine at a glance.
"""

import sys
from pathlib import Path

RECORD = Path(__file__).resolve().parents[2] / "demo" / "record"
sys.path.insert(0, str(RECORD))

import render  # noqa: E402


def wait(start, end):
    return {"scene": "s", "kind": "wait", "start": start, "end": end}


def covered(cuts):
    """True when the cuts tile [first start, last end] exactly."""
    return all(cuts[i][1] == cuts[i + 1][0] for i in range(len(cuts) - 1))


def test_with_no_waits_the_whole_recording_plays_at_normal_speed():
    assert render.cut_list(60.0, []) == [(0.0, 60.0, 1.0)]


def test_a_wait_is_sped_up_and_everything_around_it_is_not():
    cuts = render.cut_list(100.0, [wait(10.0, 60.0)])

    assert [(start, end) for start, end, _ in cuts] == [(0.0, 10.0), (10.0, 60.0), (60.0, 100.0)]
    assert [speed for *_, speed in cuts] == [1.0, 50.0 / render.WAIT_TARGET_S, 1.0]
    assert covered(cuts)


def test_a_very_long_wait_is_capped_so_the_page_does_not_just_flicker():
    cuts = render.cut_list(2000.0, [wait(0.0, 2000.0)])

    assert cuts == [(0.0, 2000.0, render.MAX_SPEED)]


def test_a_short_wait_is_left_alone():
    assert render.cut_list(30.0, [wait(10.0, 10.0 + render.MIN_WAIT_S - 0.5)]) == [(0.0, 30.0, 1.0)]


def test_overlapping_and_out_of_order_waits_never_repeat_a_second():
    cuts = render.cut_list(100.0, [wait(40.0, 70.0), wait(10.0, 50.0)])

    assert cuts[0] == (0.0, 10.0, 1.0)
    assert cuts[-1] == (70.0, 100.0, 1.0)
    assert covered(cuts)
    assert sum(end - start for start, end, _ in cuts) == 100.0


def test_a_wait_running_past_the_end_is_clipped_to_the_recording():
    cuts = render.cut_list(50.0, [wait(30.0, 80.0)])

    assert cuts[-1][1] == 50.0
    assert covered(cuts)


def test_the_filter_names_every_cut_once_and_joins_them_in_order():
    graph = render.filter_graph([(0.0, 10.0, 1.0), (10.0, 60.0, 20.0)])

    assert "trim=start=0.000:end=10.000" in graph and "trim=start=10.000:end=60.000" in graph
    assert graph.endswith("[v0][v1]concat=n=2:v=1:a=0[out]")
