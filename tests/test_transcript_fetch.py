"""_fetch_transcript_sync end to end, with only the library's HTTP boundary stubbed.

The tests in test_analyze_api.py stub `_fetch_transcript_sync` itself, so they
never execute the real construction, classification and cleanup path. These do:
they replace `YouTubeTranscriptApi.list` and `Transcript.fetch` - the two calls
that reach YouTube - so everything around them - the session, the proxy
plumbing, the choice of caption track, the exception mapping, the empty-track
guard and the try/finally close - actually runs.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

VIDEO = "dQw4w9WgXcQ"


class _Snippet:
    """Stand-in for the library's FetchedTranscriptSnippet."""

    def __init__(self, text):
        self.text = text


def _track(language_code, generated=False, video_id=VIDEO):
    """A real Transcript. Its fetch() is patched, so the URL is never requested."""
    from youtube_transcript_api import Transcript

    return Transcript(
        None, video_id, "https://www.youtube.com/api/timedtext", language_code,
        language_code, generated, [],
    )


def _tracks(*tracks, video_id=VIDEO):
    """A real TranscriptList, so track preference runs the library's own lookup."""
    from youtube_transcript_api import TranscriptList

    manual = {t.language_code: t for t in tracks if not t.is_generated}
    generated = {t.language_code: t for t in tracks if t.is_generated}
    return TranscriptList(video_id, manual, generated, [])


@pytest.fixture
def patched_fetch(fresh_main, monkeypatch):
    """Drive main._fetch_transcript_sync against stubbed YouTube calls.

    `behaviour(video_id)` stands in for downloading the chosen track: it returns
    snippets, or raises what the library would. The video offers one manually
    created English track unless `tracks` says otherwise.
    """

    def _run(behaviour, tracks=None, **env):
        main = fresh_main(**env)
        from youtube_transcript_api import Transcript, YouTubeTranscriptApi

        monkeypatch.setattr(
            YouTubeTranscriptApi,
            "list",
            lambda self, vid: tracks if tracks is not None else _tracks(_track("en", video_id=vid), video_id=vid),
        )
        monkeypatch.setattr(
            Transcript, "fetch", lambda self, preserve_formatting=False: behaviour(self.video_id)
        )
        return main

    return _run


def _ok(_vid):
    return [_Snippet("Rome fell"), _Snippet("in 476 AD.")]


def test_snippets_are_joined_into_one_transcript(patched_fetch):
    main = patched_fetch(_ok)
    assert main._fetch_transcript_sync("dQw4w9WgXcQ") == "Rome fell in 476 AD."


def test_empty_track_is_422(patched_fetch):
    main = patched_fetch(lambda vid: [])
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 422
    assert "empty" in exc.value.detail


def test_whitespace_only_track_is_422(patched_fetch):
    main = patched_fetch(lambda vid: [_Snippet("  "), _Snippet("\n")])
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 422


@pytest.mark.parametrize(
    "error_name,expected",
    [
        ("IpBlocked", 502),
        ("RequestBlocked", 502),
        ("TranscriptsDisabled", 404),
        ("VideoUnavailable", 404),
        ("VideoUnplayable", 404),
        ("AgeRestricted", 403),
        ("PoTokenRequired", 403),
        ("InvalidVideoId", 400),
    ],
)
def test_library_errors_are_classified(patched_fetch, error_name, expected):
    """Runs the real classifier, not a stub that pre-computes the answer."""
    from youtube_transcript_api import _errors as yt_errors

    cls = getattr(yt_errors, error_name)

    def raise_it(vid):
        if error_name == "VideoUnplayable":
            raise cls(vid, "Sign in to confirm", [])
        raise cls(vid)

    main = patched_fetch(raise_it)
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == expected


def test_no_transcript_found_is_404(patched_fetch):
    from youtube_transcript_api._errors import NoTranscriptFound

    main = patched_fetch(lambda vid: (_ for _ in ()).throw(NoTranscriptFound(vid, ["en"], [])))
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 404


def test_a_video_id_containing_blocked_is_not_treated_as_an_ip_block(patched_fetch):
    """Classification used to sniff str(exc), which embeds the caller-supplied
    id via the watch URL — so an id containing 'blocked' rewrote the answer."""
    from youtube_transcript_api._errors import VideoUnavailable

    main = patched_fetch(lambda vid: (_ for _ in ()).throw(VideoUnavailable(vid)))
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("xxblockedxx")
    assert exc.value.status_code == 404


def test_timeouts_become_504(patched_fetch):
    import requests

    main = patched_fetch(
        lambda vid: (_ for _ in ()).throw(requests.exceptions.ConnectTimeout("slow"))
    )
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 504


def test_connection_errors_become_502(patched_fetch):
    import requests

    main = patched_fetch(
        lambda vid: (_ for _ in ()).throw(requests.exceptions.ConnectionError("refused"))
    )
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 502


def test_unknown_errors_do_not_leak_library_internals(patched_fetch):
    """The library writes for the developer who installed it, GitHub issue link
    and all. That text used to be forwarded verbatim to visitors."""
    boom = RuntimeError("please open a GitHub issue at https://github.com/jdepoix/...")
    main = patched_fetch(lambda vid: (_ for _ in ()).throw(boom))
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert exc.value.status_code == 502
    assert "github" not in exc.value.detail.lower()


def test_proxy_hint_depends_on_whether_a_proxy_is_configured(patched_fetch):
    from youtube_transcript_api._errors import IpBlocked

    raise_blocked = lambda vid: (_ for _ in ()).throw(IpBlocked(vid))

    main = patched_fetch(raise_blocked)
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert "WEBSHARE_PROXY_USERNAME" in exc.value.detail

    main = patched_fetch(raise_blocked, WEBSHARE_PROXY_USERNAME="u", WEBSHARE_PROXY_PASSWORD="p")
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert "WEBSHARE_PROXY_USERNAME" not in exc.value.detail
    assert "proxy" in exc.value.detail.lower()


def test_transport_retries_are_disabled_on_the_session(fresh_main, monkeypatch):
    """The library mounts urllib3 Retry(total=WEBSHARE_RETRIES) on the session it
    is handed, on top of its own re-fetch loop — so the setting was applied twice
    and one call could run for (1 + retries) x the per-call timeout."""
    main = fresh_main(WEBSHARE_PROXY_USERNAME="u", WEBSHARE_PROXY_PASSWORD="p", WEBSHARE_RETRIES=5)
    from youtube_transcript_api import Transcript, YouTubeTranscriptApi

    seen = {}

    def capture(self, vid):
        seen["totals"] = {
            prefix: self._fetcher._http_client.get_adapter(prefix + "x").max_retries.total
            for prefix in ("http://", "https://")
        }
        return _tracks(_track("en"))

    monkeypatch.setattr(YouTubeTranscriptApi, "list", capture)
    monkeypatch.setattr(Transcript, "fetch", lambda self, preserve_formatting=False: [_Snippet("ok text")])
    main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert seen["totals"] == {"http://": 0, "https://": 0}


def test_the_session_is_closed_even_when_the_fetch_raises(fresh_main, monkeypatch):
    main = fresh_main()
    from youtube_transcript_api import YouTubeTranscriptApi

    closed = []
    original_close = main._TimeoutSession.close

    def spy(self):
        closed.append(True)
        return original_close(self)

    monkeypatch.setattr(main._TimeoutSession, "close", spy)
    monkeypatch.setattr(
        YouTubeTranscriptApi,
        "list",
        lambda self, vid: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(HTTPException):
        main._fetch_transcript_sync("dQw4w9WgXcQ")
    assert closed, "the session was not closed on the failure path"


# --- Which caption track gets read -------------------------------------------


def test_a_video_captioned_only_in_another_language_is_still_read(patched_fetch):
    """Asking for English alone reported this video as having no captions at
    all. That was false, and it turned away exactly the global-history videos
    the site is for."""
    main = patched_fetch(lambda vid: [_Snippet("Rom fiel 476.")], tracks=_tracks(_track("de")))
    assert main._fetch_transcript_sync(VIDEO) == "Rom fiel 476."


@pytest.mark.parametrize(
    "available,expected",
    [
        # English wins even when auto-generated: the analysis is written in English.
        ([("de", False), ("en", True)], "en"),
        ([("en-GB", False), ("en", False)], "en"),
        # Regional English is not in the preferred list, but still beats every
        # other language.
        ([("de", False), ("en-IN", True)], "en-IN"),
        # Otherwise a human-made track beats speech recognition.
        ([("fr", True), ("de", False)], "de"),
        ([("ja", True)], "ja"),
    ],
)
def test_the_best_available_track_is_chosen(fresh_main, available, expected):
    main = fresh_main()
    offered = _tracks(*(_track(code, generated) for code, generated in available))
    assert main._choose_transcript(offered).language_code == expected


def test_a_video_with_no_tracks_at_all_is_still_404(patched_fetch):
    """The fallback must not turn 'nothing to read' into some other error."""
    main = patched_fetch(lambda vid: [_Snippet("never fetched")], tracks=_tracks())
    with pytest.raises(HTTPException) as exc:
        main._fetch_transcript_sync(VIDEO)
    assert exc.value.status_code == 404
