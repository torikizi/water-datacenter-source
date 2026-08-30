#!/usr/bin/env python3
"""Build the timed VOICEVOX narration used by the submission demo video.

The script talks only to an already-running local VOICEVOX Engine. It does not
download models or start the application. Output is a mono PCM WAV matching the
duration of the video, plus a JSON manifest recording voice and timing details.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path


SPEAKER_NAME = "玄野武宏"
STYLE_NAME = "ノーマル"
STYLE_ID = 11
CREDIT = "VOICEVOX:玄野武宏(CV:ガロ)"


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


SEGMENTS = (
    Segment(
        0.35,
        7.45,
        "データセンターの設備アイティー負荷が増えると、住民が使える飲用可能な上水は、いつ減り始めるのでしょうか。",
    ),
    Segment(
        8.05,
        17.45,
        "印西を題材に、六月一日から九月十日までの百二日を、再生水ゼロパーセント、比例配分で比較します。",
    ),
    Segment(
        18.05,
        25.40,
        "データセンターなしでは、住民需要を全量供給し、不足はゼロです。",
    ),
    Segment(
        26.05,
        36.55,
        "設備負荷を百二十四点六メガワットにすると、期間内の不足はゼロ。ただし期末の水は、ゼロ点ゼロ七五メガリットルまで減ります。",
    ),
    Segment(
        37.05,
        49.35,
        "わずかゼロ点一メガワット上げた百二十四点七メガワットでは、最終日に初めて、ゼロ点ゼロ六八メガリットル不足します。",
    ),
    Segment(
        50.15,
        57.65,
        "次に、二百五十メガワットのケースを再生します。住民の水不足は、三十九日目から始まります。",
    ),
    Segment(
        58.35,
        65.35,
        "百二日累計で、住民向け上水は、データセンターなしより百五十三点五三メガリットル、約七パーセント減ります。",
    ),
    Segment(
        65.75,
        75.15,
        "設備負荷が増えるほど、水利用型冷却の需要も増えます。これは現実の印西市の予測ではなく、条件差を比べるシミュレーションです。",
    ),
)


def request_bytes(
    api_base: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
    method: str = "GET",
) -> bytes:
    query = urllib.parse.urlencode(params or {})
    url = f"{api_base.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    data = None
    headers: dict[str, str] = {}
    if method == "POST":
        if payload is None:
            data = b""
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def request_json(
    api_base: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
    method: str = "GET",
) -> object:
    return json.loads(
        request_bytes(
            api_base,
            path,
            params=params,
            payload=payload,
            method=method,
        ).decode("utf-8")
    )


def validate_voice(api_base: str) -> str:
    version = request_json(api_base, "/version")
    speakers = request_json(api_base, "/speakers")
    if not isinstance(speakers, list):
        raise RuntimeError("VOICEVOX /speakers returned an unexpected value")
    for speaker in speakers:
        if speaker.get("name") != SPEAKER_NAME:
            continue
        for style in speaker.get("styles", []):
            if style.get("name") == STYLE_NAME and style.get("id") == STYLE_ID:
                return str(version)
    raise RuntimeError(
        f"Required voice not found: {SPEAKER_NAME} / {STYLE_NAME} / {STYLE_ID}"
    )


def synthesize(api_base: str, text: str, speed_scale: float) -> bytes:
    query = request_json(
        api_base,
        "/audio_query",
        params={"text": text, "speaker": STYLE_ID},
        method="POST",
    )
    if not isinstance(query, dict):
        raise RuntimeError("VOICEVOX /audio_query returned an unexpected value")
    query["speedScale"] = speed_scale
    query["pitchScale"] = -0.02
    query["intonationScale"] = 1.0
    query["volumeScale"] = 1.0
    query["prePhonemeLength"] = 0.10
    query["postPhonemeLength"] = 0.14
    query["outputSamplingRate"] = 24000
    query["outputStereo"] = False
    return request_bytes(
        api_base,
        "/synthesis",
        params={"speaker": STYLE_ID, "enable_interrogative_upspeak": "false"},
        payload=query,
        method="POST",
    )


def read_wav(wav_bytes: bytes) -> tuple[wave._wave_params, bytes, float]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        params = reader.getparams()
        frames = reader.readframes(reader.getnframes())
    duration = params.nframes / params.framerate
    return params, frames, duration


def fit_segment(api_base: str, segment: Segment) -> tuple[bytes, float, float]:
    target = segment.end - segment.start
    speed = 1.0
    wav_bytes = b""
    duration = 0.0
    for _ in range(4):
        wav_bytes = synthesize(api_base, segment.text, speed)
        _, _, duration = read_wav(wav_bytes)
        desired = target * 0.93
        adjusted = speed * duration / desired
        adjusted = max(0.90, min(1.55, adjusted))
        if duration <= target and abs(adjusted - speed) < 0.025:
            break
        speed = adjusted
    if duration > target:
        raise RuntimeError(
            f"Narration segment does not fit {segment.start:.2f}-{segment.end:.2f}: "
            f"{duration:.3f}s at speedScale {speed:.3f}"
        )
    return wav_bytes, speed, duration


def build(args: argparse.Namespace) -> None:
    engine_version = validate_voice(args.api)
    temp_dir = Path(args.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    sample_rate = 24000
    sample_width = 2
    channels = 1
    total_frames = math.ceil(args.video_duration * sample_rate)
    timeline = bytearray(total_frames * sample_width * channels)
    manifest_segments: list[dict[str, object]] = []

    for index, segment in enumerate(SEGMENTS, start=1):
        wav_bytes, speed, duration = fit_segment(args.api, segment)
        params, frames, checked_duration = read_wav(wav_bytes)
        if (
            params.framerate != sample_rate
            or params.sampwidth != sample_width
            or params.nchannels != channels
        ):
            raise RuntimeError(f"Unexpected WAV format in segment {index}: {params}")
        start_frame = round(segment.start * sample_rate)
        end_frame = start_frame + params.nframes
        if end_frame > total_frames:
            raise RuntimeError(f"Segment {index} extends past the video duration")
        byte_start = start_frame * sample_width * channels
        byte_end = end_frame * sample_width * channels
        timeline[byte_start:byte_end] = frames

        segment_path = temp_dir / f"segment-{index:02d}.wav"
        segment_path.write_bytes(wav_bytes)
        manifest_segments.append(
            {
                "index": index,
                "start_seconds": segment.start,
                "window_end_seconds": segment.end,
                "audio_duration_seconds": round(checked_duration, 3),
                "speed_scale": round(speed, 4),
                "text": segment.text,
            }
        )
        print(
            f"segment {index}: {segment.start:05.2f}s, "
            f"duration={duration:05.2f}s, speed={speed:.3f}"
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(timeline)

    manifest = {
        "generator": "VOICEVOX",
        "engine_version": engine_version,
        "speaker": SPEAKER_NAME,
        "style": STYLE_NAME,
        "style_id": STYLE_ID,
        "required_credit": CREDIT,
        "video_duration_seconds": args.video_duration,
        "audio_format": {
            "codec": "PCM signed 16-bit little-endian",
            "sample_rate_hz": sample_rate,
            "channels": channels,
        },
        "segments": manifest_segments,
    }
    manifest_path = Path(args.manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output_path}")
    print(f"wrote {manifest_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:50021")
    parser.add_argument(
        "--output", default="submission/voicevox-narration.wav"
    )
    parser.add_argument(
        "--manifest", default="submission/voicevox-narration.json"
    )
    parser.add_argument("--temp-dir", default=".tmp-voicevox")
    parser.add_argument("--video-duration", type=float, default=75.533333)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
