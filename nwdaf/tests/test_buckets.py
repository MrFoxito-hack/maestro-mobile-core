import pytest
from app.ingestion.buckets import regularize


def points(times):
    return [{'bucket_epoch': t, 'value': t / 10} for t in times]


def test_closed_buckets_only_and_reproducible_mean():
    result = regularize(points(range(0, 650, 30)), interval=300, now=650, max_gap=30)
    assert [p['bucket_epoch'] for p in result] == [300, 600]
    assert result[0]['value'] == 13.5
    assert result[0]['source_count'] == 10


def test_missing_bucket_never_filled():
    result = regularize(points([*range(0, 300, 30), *range(600, 900, 30)]),
                        interval=300, now=900, max_gap=30)
    assert [p['bucket_epoch'] for p in result] == [900]


def test_hole_inside_latest_bucket_prevents_forecast():
    result = regularize(points([*range(0, 300, 30), 300, 330, 570]),
                        interval=300, now=600, max_gap=30)
    assert result == []


def test_duplicate_timestamp_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        regularize(points([0, 0]), interval=300, now=600, max_gap=30)
