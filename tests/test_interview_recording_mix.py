"""The interview recording must contain BOTH voices: the candidate's microphone and the AI interviewer. Structural checks on the
wiring (the audio mixing itself was verified in a real browser): every source of interviewer audio announces its track on one
event, and the recording is built from the mixer's single output track."""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENT = "jobpilot-ai-audio-track"


def _js(name):
    with open(os.path.join(ROOT, "frontend", "js", name), encoding="utf-8") as f:
        return f.read()


def test_live_mode_announces_the_interviewers_livekit_audio_track():
    avatar = _js("interview-avatar-v2.js")
    assert EVENT in avatar and "getRemoteAudioTracks" in avatar and "mediaStreamTrack" in avatar


def test_standard_voice_mode_announces_each_spoken_question_clip():
    voice = _js("interview-voice-v2.js")
    assert EVENT in voice and "captureStream" in voice


def test_the_recording_uses_the_mixed_audio_not_the_microphone_alone():
    page = _js("ai-interview-v2.js")

    assert "function buildRecordingAudioMix" in page and EVENT in page
    assert "createMediaStreamDestination" in page and "createMediaStreamSource" in page
    assert "recordingMix.stream.getAudioTracks()" in page                    # the recorder gets the mixer's output
    assert "destination.stream" in page

    mix = page[page.index("function buildRecordingAudioMix"):page.index("async function startMicAndRecording")]
    assert "ctx.destination" not in mix                                      # not connected to the speakers: no double playback
    assert "added.has(track.id)" in mix                                      # a track announced twice is mixed once
    assert "recordingMix.close()" in page                                    # the mixer is released after the recorder stops
