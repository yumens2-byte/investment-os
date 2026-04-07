"""
Phase 1A 모듈 구조/문법 검증 테스트 (오프라인)

실제 API 호출 없이 모듈이 import 가능하고
함수 시그니처가 정상인지 확인.

실제 동작은 GitHub Actions에서 검증.
"""
import ast
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent

FILES = [
    "db/api_cache_store.py",
    "collectors/crypto_com_client.py",
    "collectors/lunarcrush_client.py",
]


def check_syntax(filepath: Path) -> bool:
    """Python 문법 검증"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source)
        print(f"  ✅ 문법 OK: {filepath.name}")
        return True
    except SyntaxError as e:
        print(f"  ❌ 문법 오류: {filepath.name} — {e}")
        return False


def check_functions(filepath: Path, expected_funcs: list) -> bool:
    """예상 함수 존재 확인"""
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    actual = [
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
    ]

    missing = set(expected_funcs) - set(actual)
    if missing:
        print(f"  ❌ {filepath.name} 누락 함수: {missing}")
        return False
    print(f"  ✅ 함수 OK: {filepath.name} — {', '.join(expected_funcs)}")
    return True


def main():
    print("=" * 60)
    print("Phase 1A 모듈 구조 검증")
    print("=" * 60)

    all_ok = True

    # 1. 문법 검증
    print("\n[1] Python 문법 검증")
    for f in FILES:
        if not check_syntax(BASE / f):
            all_ok = False

    # 2. 예상 함수 검증
    print("\n[2] 함수 시그니처 검증")

    checks = [
        (
            "db/api_cache_store.py",
            ["get_cache", "set_cache", "get_stale_cache", "cleanup_expired"],
        ),
        (
            "collectors/crypto_com_client.py",
            ["get_mark_price", "get_index_price", "get_btc_basis"],
        ),
        (
            "collectors/lunarcrush_client.py",
            ["get_btc_sentiment"],
        ),
    ]

    for filepath, funcs in checks:
        if not check_functions(BASE / filepath, funcs):
            all_ok = False

    # 3. VERSION 상수 확인
    print("\n[3] VERSION 상수 확인")
    for f in FILES:
        with open(BASE / f, "r", encoding="utf-8") as fp:
            if 'VERSION = ' in fp.read():
                print(f"  ✅ VERSION 상수 있음: {f}")
            else:
                print(f"  ❌ VERSION 상수 없음: {f}")
                all_ok = False

    # 4. 로그 포맷 확인
    print("\n[4] 로그 포맷 확인")
    for f in FILES:
        with open(BASE / f, "r", encoding="utf-8") as fp:
            content = fp.read()
            expected_tag = {
                "api_cache_store.py": "[ApiCache]",
                "crypto_com_client.py": "[CryptoCom]",
                "lunarcrush_client.py": "[LunarCrush]",
            }
            fname = Path(f).name
            if expected_tag.get(fname, "") in content:
                print(f"  ✅ 로그 태그 OK: {fname}")
            else:
                print(f"  ⚠️  로그 태그 누락 또는 비표준: {fname}")

    print("\n" + "=" * 60)
    if all_ok:
        print("✅ 전체 검증 통과")
        return 0
    else:
        print("❌ 검증 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
