"""Dedicated CLI entrypoint for the Naver blog PREPARE_ONLY pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from blog.service import prepare_from_file


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CORE_DATA = BASE_DIR / "data" / "outputs" / "core_data.json"
logger = logging.getLogger("blog_main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python blog_main.py",
        description="Investment OS Naver Blog — PREPARE_ONLY",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser(
        "prepare",
        help="제목·본문·태그·출처·검증 보고서 생성",
    )
    prepare.add_argument(
        "--session",
        choices=["morning", "intraday", "close", "full", "weekly", "narrative"],
        default="morning",
    )
    prepare.add_argument(
        "--core-data",
        default=str(DEFAULT_CORE_DATA),
        help="core_data.json 경로",
    )
    prepare.add_argument(
        "--output-dir",
        default=None,
        help="산출물 루트 (기본: data/outputs/naver_blog)",
    )
    prepare.add_argument(
        "--dry-run",
        choices=["true", "false"],
        default="true",
        help="true=승인 선택, false=승인 권고. 둘 다 PREPARE_ONLY",
    )
    return parser


def run_prepare(args: argparse.Namespace) -> int:
    try:
        result = prepare_from_file(
            args.core_data,
            session=args.session,
            output_dir=args.output_dir,
            dry_run=args.dry_run.lower() == "true",
        )
    except Exception as exc:
        logger.critical("blog prepare 실패: %s", exc, exc_info=True)
        return 1

    print("\n  [blog prepare] 결과")
    for key, value in result.items():
        print(f"    {key}: {value}")
    print()
    return 0 if result.get("success") else 2


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.command == "prepare":
        sys.exit(run_prepare(args))
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
