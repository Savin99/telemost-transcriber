import argparse
from pathlib import Path
import sys

from app.audio_utils import (
    default_recordings_dir,
    default_voice_bank_dir,
    SUPPORTED_AUDIO_EXTENSIONS,
)
from app.transcribe import TranscriberPipeline
from app.voice_bank import VoiceBank


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the speaker voice bank and inspect meeting diarization.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Device to use for inference (default: auto).",
    )
    parser.add_argument(
        "--voice-bank-dir",
        default=default_voice_bank_dir(),
        help="Directory for index.json, embeddings.npz, and meeting bundles.",
    )
    parser.add_argument(
        "--recordings-dir",
        default=default_recordings_dir(),
        help="Directory with saved meeting recordings for interactive labeling.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    enroll_parser = subparsers.add_parser(
        "enroll", help="Enroll a speaker from audio files."
    )
    enroll_parser.add_argument("name")
    enroll_parser.add_argument("audio_paths", nargs="+")

    enroll_meeting_parser = subparsers.add_parser(
        "enroll-from-meeting",
        help="Enroll a speaker from a diarized meeting bundle.",
    )
    enroll_meeting_parser.add_argument("name")
    enroll_meeting_parser.add_argument("audio_path")
    enroll_meeting_parser.add_argument("--speaker", dest="speaker_label")
    enroll_meeting_parser.add_argument("--num-speakers", type=int)
    enroll_meeting_parser.add_argument("--min-speakers", type=int)
    enroll_meeting_parser.add_argument("--max-speakers", type=int)

    label_meeting_parser = subparsers.add_parser(
        "label-meeting",
        help="Interactively label speaker clusters from a saved meeting recording.",
    )
    label_meeting_parser.add_argument("audio_path", nargs="?")
    label_meeting_parser.add_argument("--num-speakers", type=int)
    label_meeting_parser.add_argument("--min-speakers", type=int)
    label_meeting_parser.add_argument("--max-speakers", type=int)
    label_meeting_parser.add_argument(
        "--samples-per-speaker",
        type=int,
        default=3,
        help="How many sample clips to export for each detected speaker.",
    )
    label_meeting_parser.add_argument(
        "--sample-max-seconds",
        type=float,
        default=12.0,
        help="Maximum duration for each exported sample clip.",
    )

    subparsers.add_parser("list-speakers", help="List enrolled speakers.")

    remove_parser = subparsers.add_parser(
        "remove", help="Remove a speaker from the voice bank."
    )
    remove_parser.add_argument("name")

    test_parser = subparsers.add_parser(
        "test-identify",
        help="Run diarization + identification without transcription output.",
    )
    test_parser.add_argument("audio_path")
    test_parser.add_argument("--num-speakers", type=int)
    test_parser.add_argument("--min-speakers", type=int)
    test_parser.add_argument("--max-speakers", type=int)

    merge_parser = subparsers.add_parser(
        "merge-duplicates",
        help="Найти пары подозрительно похожих спикеров и склеить их интерактивно.",
    )
    merge_parser.add_argument(
        "--threshold",
        type=float,
        default=0.70,
        help="Минимальная косинусная схожесть голосов (0..1). По умолчанию 0.70.",
    )
    merge_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать пары-кандидаты, без интерактивного мерджа.",
    )

    return parser


def _format_timestamp(seconds: float) -> str:
    seconds_int = int(seconds)
    minutes, seconds_part = divmod(seconds_int, 60)
    hours, minutes_part = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes_part:02d}:{seconds_part:02d}"
    return f"{minutes_part}:{seconds_part:02d}"


def _build_runtime(args) -> tuple[TranscriberPipeline, VoiceBank]:
    pipeline = TranscriberPipeline(device=args.device)
    voice_bank = VoiceBank(
        args.voice_bank_dir,
        speaker_identifier=pipeline.speaker_identifier,
        min_segment_seconds=pipeline.min_embedding_segment_seconds,
    )
    pipeline._voice_bank = voice_bank
    return pipeline, voice_bank


def _ensure_bundle(pipeline: TranscriberPipeline, audio_path: str, args):
    bundle = pipeline.voice_bank.load_meeting_bundle(audio_path)
    if bundle is not None:
        return bundle

    return pipeline.inspect_speakers(
        audio_path,
        num_speakers=getattr(args, "num_speakers", None),
        min_speakers=getattr(args, "min_speakers", None),
        max_speakers=getattr(args, "max_speakers", None),
    )


def _iter_display_labels(bundle: dict) -> list[str]:
    ordered_labels = list(bundle.get("ordered_labels") or [])
    for speaker_label in bundle.get("cluster_profiles", {}):
        if speaker_label not in ordered_labels:
            ordered_labels.append(speaker_label)
    return ordered_labels


def _summarize_profile(profile: dict) -> str:
    segments = profile.get("embedding_segments") or profile.get("segments") or []
    if not segments:
        return "no segments"
    preview = []
    for segment in segments[:3]:
        preview.append(
            f"{_format_timestamp(float(segment['start']))}-{_format_timestamp(float(segment['end']))}"
        )
    if len(segments) > 3:
        preview.append("...")
    return ", ".join(preview)


def _current_assignment(bundle: dict, speaker_label: str) -> tuple[str, float, bool]:
    profile = bundle["cluster_profiles"].get(speaker_label, {})
    assignment = bundle.get("mapping", {}).get(speaker_label)
    if assignment is None and profile.get("assignment"):
        assignment_data = profile["assignment"]
        return (
            assignment_data.get("name", speaker_label),
            float(assignment_data.get("confidence", 0.0)),
            bool(assignment_data.get("is_known")),
        )
    if assignment is not None:
        return assignment.name, assignment.confidence, assignment.is_known
    return speaker_label, 0.0, False


def _prompt_for_speaker(bundle: dict) -> str:
    labels = _iter_display_labels(bundle)
    if not labels:
        raise RuntimeError("No speaker clusters available in the meeting bundle")

    print("Available speaker clusters:")
    for index, speaker_label in enumerate(labels, start=1):
        profile = bundle["cluster_profiles"].get(speaker_label, {})
        current_name, current_confidence, _ = _current_assignment(bundle, speaker_label)
        print(
            f"  [{index}] {speaker_label} -> {current_name} "
            f"(confidence={current_confidence:.4f}) "
            f"[{_summarize_profile(profile)}]"
        )

    while True:
        choice = input("Choose speaker number: ").strip()
        if not choice.isdigit():
            print("Enter a valid number.")
            continue
        choice_index = int(choice)
        if 1 <= choice_index <= len(labels):
            return labels[choice_index - 1]
        print("Choice out of range.")


def _print_identification(bundle: dict):
    labels = _iter_display_labels(bundle)
    if not labels:
        print("No speaker clusters found.")
        return

    for speaker_label in labels:
        profile = bundle["cluster_profiles"].get(speaker_label, {})
        name, confidence, is_known = _current_assignment(bundle, speaker_label)

        print(
            f"{speaker_label}: {name} "
            f"(confidence={confidence:.4f}, known={'yes' if is_known else 'no'}) "
            f"[{_summarize_profile(profile)}]"
        )


def _list_recordings(recordings_dir: str) -> list[Path]:
    root = Path(recordings_dir)
    if not root.exists():
        return []

    recordings = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    recordings.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
    return recordings


def _resolve_audio_path(args) -> str:
    if getattr(args, "audio_path", None):
        return args.audio_path

    recordings = _list_recordings(args.recordings_dir)
    if not recordings:
        raise RuntimeError(
            f"No recordings found in {args.recordings_dir}. "
            "Pass an explicit audio path or set --recordings-dir."
        )

    print("Available recordings:")
    for index, path in enumerate(recordings, start=1):
        print(f"  [{index}] {path}")

    while True:
        choice = input("Choose recording number: ").strip()
        if not choice.isdigit():
            print("Enter a valid number.")
            continue
        choice_index = int(choice)
        if 1 <= choice_index <= len(recordings):
            return str(recordings[choice_index - 1])
        print("Choice out of range.")


def _label_meeting_interactively(
    voice_bank: VoiceBank,
    audio_path: str,
    bundle: dict,
    samples_per_speaker: int,
    sample_max_seconds: float,
):
    sample_paths = voice_bank.export_meeting_samples(
        audio_path,
        bundle,
        samples_per_speaker=samples_per_speaker,
        sample_max_seconds=sample_max_seconds,
    )
    enrolled: list[tuple[str, str]] = []

    for speaker_label in _iter_display_labels(bundle):
        profile = bundle["cluster_profiles"].get(speaker_label, {})
        current_name, current_confidence, is_known = _current_assignment(
            bundle, speaker_label
        )
        print("")
        print(
            f"{speaker_label}: current={current_name} "
            f"(confidence={current_confidence:.4f}, known={'yes' if is_known else 'no'})"
        )
        print(f"Segments: {_summarize_profile(profile)}")
        samples = sample_paths.get(speaker_label) or []
        if samples:
            print("Sample clips:")
            for sample in samples:
                print(f"  - {sample}")
        else:
            print("Sample clips: none")

        answer = input(
            "Name for this speaker "
            "[Enter/skip = skip, existing name = refresh from this meeting]: "
        ).strip()
        if not answer or answer.lower() in {"skip", "s", "-"}:
            continue

        voice_bank.learn_from_diarization_label(
            name=answer,
            audio_path=audio_path,
            diarization=bundle,
            speaker_label=speaker_label,
        )
        enrolled.append((speaker_label, answer))
        print(f"Enrolled {answer} from {speaker_label}")

    print("")
    if not enrolled:
        print("No speakers were enrolled.")
        return

    print("Enrolled speakers:")
    for speaker_label, name in enrolled:
        print(f"  {speaker_label} -> {name}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    pipeline, voice_bank = _build_runtime(args)

    if args.command == "enroll":
        voice_bank.enroll(args.name, args.audio_paths)
        print(f"Enrolled {args.name} into {voice_bank.root_dir}")
        return 0

    if args.command == "enroll-from-meeting":
        bundle = _ensure_bundle(pipeline, args.audio_path, args)
        speaker_label = args.speaker_label or _prompt_for_speaker(bundle)
        voice_bank.learn_from_diarization_label(
            name=args.name,
            audio_path=args.audio_path,
            diarization=bundle,
            speaker_label=speaker_label,
        )
        print(
            f"Enrolled {args.name} from {speaker_label} "
            f"using bundle {bundle.get('bundle_dir', voice_bank.meeting_dir_for(args.audio_path))}"
        )
        return 0

    if args.command == "label-meeting":
        audio_path = _resolve_audio_path(args)
        bundle = _ensure_bundle(pipeline, audio_path, args)
        _print_identification(bundle)
        print(f"Bundle saved to {bundle['bundle_dir']}")
        _label_meeting_interactively(
            voice_bank=voice_bank,
            audio_path=audio_path,
            bundle=bundle,
            samples_per_speaker=args.samples_per_speaker,
            sample_max_seconds=args.sample_max_seconds,
        )
        return 0

    if args.command == "list-speakers":
        speakers = voice_bank.list_speakers()
        if not speakers:
            print("Voice bank is empty.")
            return 0
        for speaker in speakers:
            print(
                f"{speaker['name']}: "
                f"embeddings={speaker['num_embeddings']}, "
                f"enrolled_at={speaker['enrolled_at']}, "
                f"updated_at={speaker['updated_at']}"
            )
        return 0

    if args.command == "remove":
        voice_bank.remove(args.name)
        print(f"Removed {args.name} from {voice_bank.root_dir}")
        return 0

    if args.command == "test-identify":
        bundle = pipeline.inspect_speakers(
            args.audio_path,
            num_speakers=args.num_speakers,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
        )
        _print_identification(bundle)
        print(f"Bundle saved to {bundle['bundle_dir']}")
        return 0

    if args.command == "merge-duplicates":
        return _merge_duplicates_interactive(
            voice_bank,
            threshold=args.threshold,
            dry_run=args.dry_run,
        )

    parser.error(f"Unknown command: {args.command}")
    return 1


def _format_pair(pair: dict) -> str:
    return (
        f"  [1] {pair['name_a']} "
        f"(emb={pair['num_embeddings_a']}, enrolled={pair['enrolled_at_a']})\n"
        f"  [2] {pair['name_b']} "
        f"(emb={pair['num_embeddings_b']}, enrolled={pair['enrolled_at_b']})\n"
        f"      голос: {pair['voice_sim']:.3f}   имя: {pair['name_sim']:.3f}"
    )


def _merge_duplicates_interactive(voice_bank, threshold: float, dry_run: bool) -> int:
    pairs = voice_bank.find_duplicate_candidates(voice_threshold=threshold)
    if not pairs:
        print(f"Дубли не найдены (порог голоса {threshold:.2f}).")
        return 0

    print(f"Найдено пар-кандидатов: {len(pairs)} (порог голоса {threshold:.2f})")
    if dry_run:
        for idx, pair in enumerate(pairs, start=1):
            print(f"\n#{idx}")
            print(_format_pair(pair))
        return 0

    merged: set[str] = set()
    for idx, pair in enumerate(pairs, start=1):
        if pair["name_a"] in merged or pair["name_b"] in merged:
            continue
        print(f"\n#{idx}")
        print(_format_pair(pair))
        answer = (
            input("Оставить [1/2] и слить второго, [s]kip, [q]uit: ").strip().lower()
        )
        if answer in {"q", "quit"}:
            break
        if answer in {"", "s", "skip"}:
            continue
        if answer == "1":
            keep, drop = pair["name_a"], pair["name_b"]
        elif answer == "2":
            keep, drop = pair["name_b"], pair["name_a"]
        else:
            print("Непонятный ввод — skip.")
            continue
        voice_bank.merge_speakers(keep_name=keep, merge_name=drop)
        merged.add(drop)
        print(f"✓ {drop} → {keep}")

    print("\nГотово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
