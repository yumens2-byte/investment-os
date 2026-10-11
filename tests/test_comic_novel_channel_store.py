from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from db.comic_novel_channel_store import ComicNovelChannelStore, source_content_hash


def test_rpc_failure_propagates_no_false_commit():
    client = MagicMock()
    client.rpc.return_value.execute.side_effect = ConnectionError('DB down')
    with pytest.raises(ConnectionError):
        ComicNovelChannelStore(client).finalize_publication(1, 'page_post', datetime.now(timezone.utc))


@pytest.mark.parametrize('ids,count', [(['DRY_RUN'],1), (['FAIL'],1), (['123'],2), (['123','123'],2), ([],0), (['123'],True)])
def test_partial_or_simulated_x_never_written(ids, count):
    client = MagicMock()
    with pytest.raises(ValueError):
        ComicNovelChannelStore(client).record_x_publication('2026-10-12', ids, count, 'title', 'EP.1', 'body')
    client.rpc.assert_not_called()


def test_x_confirmation_keeps_canonical_hash_not_fb_formatted_body():
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data={'status':'ok'})
    assert ComicNovelChannelStore(client).record_x_publication('2026-10-12', ['123'], 1, 't', 'e', 'b')
    params = client.rpc.call_args.args[1]
    assert params['p_source_hash'] == source_content_hash('t','e','b')
    assert params['p_post_ids'] == ['123']


def test_naive_reconcile_timestamp_rejected():
    client = MagicMock()
    with pytest.raises(ValueError):
        ComicNovelChannelStore(client).reconcile_publication(1,'p',datetime(2026,10,16),'operator','checked')
    client.rpc.assert_not_called()


def test_unexpected_rpc_result_is_not_success():
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data=None)
    with pytest.raises(RuntimeError):
        ComicNovelChannelStore(client).mark_sending(1,'hash')


def test_finalize_blocked_result_is_not_success():
    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data={'status':'blocked'})
    with pytest.raises(RuntimeError):
        ComicNovelChannelStore(client).finalize_publication(1,'p',datetime.now(timezone.utc))
