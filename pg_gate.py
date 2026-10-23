"""Cổng lệnh cho agent Claude Code dựng sơ đồ (graphs.run_claude): lệnh shell DUY NHẤT agent được chạy là

    <python của VietPaper> -I <đường dẫn file này> read|check|figure|fmt …

Chạy bằng `-I` (bỏ thư mục hiện tại khỏi sys.path, bỏ biến PYTHON*): agent ghi được file trong thư mục làm việc, nên
`python -m paper_graph` ở đó sẽ nạp gói `paper_graph/` giả do agent viết (đã tái hiện, differential-review F1). File
này nằm ngoài thư mục làm việc, agent không sửa được (công cụ file bị `--restricted` giam trong thư mục đó).

Cổng tự dựng tham số cho paper_graph từ một khuôn cố định: chỉ đúng các file trong thư mục hiện tại (`paper.blocks.json`,
`paper.graph.json`, `src.pdf`, `base.graph.json`), không nhận đường dẫn nào khác, không `--base git`, không `--blocks`
(F2: các lệnh này đọc, ghi file theo đường dẫn được đưa, ngoài sự giam của `--restricted`)."""
import re
import sys
from pathlib import Path

BLOCKS, GRAPH, PDF, BASE = "paper.blocks.json", "paper.graph.json", "src.pdf", "base.graph.json"
FIG_ID = re.compile(r"p\d{1,4}-[bf]\d{1,4}")
PAGES = re.compile(r"\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*")


class Refused(Exception):
    pass


def _here(name: str) -> str:
    """Chỉ nhận đúng tên file cố định trong thư mục hiện tại (cũng chấp nhận ./tên), và file đó không phải symlink."""
    if name.removeprefix("./") not in (BLOCKS, GRAPH, PDF, BASE):
        raise Refused(f"chỉ dùng được {BLOCKS}, {GRAPH}, {PDF}, {BASE} trong thư mục hiện tại, không nhận {name!r}")
    p = Path.cwd() / name.removeprefix("./")
    if p.is_symlink():
        raise Refused(f"{name} là symlink")
    return p.name


def build_argv(args: list[str]) -> list[str]:
    """Tham số người gọi (agent) → tham số an toàn cho paper_graph.cli.main; sai khuôn thì Refused."""
    if not args:
        raise Refused("cần lệnh: read, check, figure hay fmt")
    cmd, rest = args[0], args[1:]
    if cmd == "read":
        if not rest or _here(rest[0]) != BLOCKS:
            raise Refused(f"read {BLOCKS} [--pages 1-5] [--find \"cụm\"]")
        out, i = ["read", BLOCKS], 1
        while i < len(rest):
            if rest[i] == "--pages" and i + 1 < len(rest) and PAGES.fullmatch(rest[i + 1]):
                out += ["--pages", rest[i + 1]]
            elif rest[i] == "--find" and i + 1 < len(rest) and len(rest[i + 1]) <= 300:
                out += ["--find", rest[i + 1]]
            else:
                raise Refused(f"read: không nhận {rest[i]!r}")
            i += 2
        return out
    if cmd == "check":
        if not rest or _here(rest[0]) != GRAPH:
            raise Refused(f"check {GRAPH} [--pdf {PDF}] [--base {BASE}]")
        out, i = ["check", GRAPH, "--blocks", BLOCKS], 1
        while i < len(rest):
            if rest[i] == "--pdf" and i + 1 < len(rest) and _here(rest[i + 1]) == PDF:
                out += ["--pdf", PDF]
            elif rest[i] == "--base" and i + 1 < len(rest) and _here(rest[i + 1]) == BASE:
                out += ["--base", BASE]
            elif rest[i] == "--json":
                out.append("--json")
                i -= 1
            else:
                raise Refused(f"check: không nhận {rest[i]!r}")
            i += 2
        return out
    if cmd == "figure":
        if not rest or _here(rest[0]) != BLOCKS:
            raise Refused(f"figure {BLOCKS} <id> … [--pdf {PDF}]")
        ids, i = [], 1
        while i < len(rest):
            if rest[i] == "--pdf" and i + 1 < len(rest) and _here(rest[i + 1]) == PDF:
                i += 2
            elif FIG_ID.fullmatch(rest[i]) and len(ids) < 20:
                ids.append(rest[i])
                i += 1
            else:
                raise Refused(f"figure: không nhận {rest[i]!r} (id dạng p8-f1, p8-b3; tối đa 20)")
        if not ids:
            raise Refused("figure: cần ít nhất một id")
        return ["figure", BLOCKS, *ids, "--pdf", PDF]
    if cmd == "fmt":
        if len(rest) != 1 or _here(rest[0]) != GRAPH:
            raise Refused(f"fmt {GRAPH}")
        return ["fmt", GRAPH]
    raise Refused(f"lệnh {cmd!r} không có: read, check, figure, fmt")


def main() -> int:
    try:
        argv = build_argv(sys.argv[1:])
    except Refused as e:
        print(f"pg_gate từ chối: {e}", file=sys.stderr)
        return 2
    from paper_graph.cli import main as pg_main  # noqa: PLC0415 - nạp sau khi kiểm (gói cài trong venv, không từ cwd)
    return pg_main(argv)


if __name__ == "__main__":
    sys.exit(main())
