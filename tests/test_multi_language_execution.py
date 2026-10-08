"""
Task 5C, Complete Multi-Language Execution Engine — Steps 6, 7, 8.

REAL execution only. No mocks. Every solution below is actually
compiled/interpreted and run through its real LanguageRunner — now a
direct Docker sandbox (docker_sandbox_runner.py), one throwaway
container per submission using Docker's own isolation, rather than a
bare local subprocess or either of the two third-party judge
services tried and abandoned first (Piston, then Judge0 — see
docker_sandbox_client.py's own header for why). Never touches
jobpilot.db.

Requires a reachable Docker daemon with the core language images
pulled (python/node/java/gcc/golang/rust/php/ruby/swift — see
docker_sandbox_runner.py) — the whole file is skipped, not failed,
when none is reachable. Any language without a registered runner
(there are none left unregistered as of the 13-language set, but
the guard stays as a safety net) is simply skipped by each test's
own `if lang not in RUNNER_REGISTRY` guard.
"""

import pytest

from backend.app.schemas.execution_result import ExecutionRequest
from backend.app.services.assessment import docker_sandbox_client as sandbox
from backend.app.services.assessment.language_runner import RUNNER_REGISTRY

pytestmark = pytest.mark.skipif(
    not sandbox.is_available(force=True),
    reason="Docker is not reachable -- see docker_sandbox_client.py's own header for setup",
)

# Step 6: input 5 -> output 25 (square)
_SQUARE_VALID = {
    "python": "n = int(input())\nprint(n * n)",
    "javascript": "const n = parseInt(require('fs').readFileSync(0, 'utf-8').trim());console.log(n * n);",
    "typescript": "const n: number = parseInt(require('fs').readFileSync(0, 'utf-8').trim());console.log(n * n);",
    "csharp": "using System;\nclass Program { static void Main() { int n = int.Parse(Console.ReadLine()); Console.WriteLine(n * n); } }\n",
    "java": "import java.util.Scanner;\npublic class Solution { public static void main(String[] args) { Scanner sc = new Scanner(System.in); int n = sc.nextInt(); System.out.println(n * n); } }\n",
    "c": "#include <stdio.h>\nint main() { int n; scanf(\"%d\", &n); printf(\"%d\\n\", n * n); return 0; }\n",
    "cpp": "#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; cout << n * n << endl; return 0; }\n",
    "go": "package main\nimport (\"fmt\")\nfunc main() { var n int; fmt.Scan(&n); fmt.Println(n * n) }\n",
    "rust": "use std::io;\nfn main() { let mut s = String::new(); io::stdin().read_line(&mut s).unwrap(); let n: i64 = s.trim().parse().unwrap(); println!(\"{}\", n * n); }\n",
    "php": "<?php\n$n = (int)trim(fgets(STDIN));\necho $n * $n . \"\\n\";\n",
    "ruby": "n = gets.to_i\nputs n * n\n",
    "kotlin": "fun main() { val n = readLine()!!.trim().toInt(); println(n * n) }\n",
    "swift": "let n = Int(readLine()!)!\nprint(n * n)\n",
}

_SQUARE_WRONG_ANSWER = {
    "python": "n = int(input())\nprint(n + 1)",  # deliberately wrong
    "javascript": "const n = parseInt(require('fs').readFileSync(0, 'utf-8').trim());console.log(n + 1);",
    "csharp": "using System;\nclass Program { static void Main() { int n = int.Parse(Console.ReadLine()); Console.WriteLine(n + 1); } }\n",
    "java": "import java.util.Scanner;\npublic class Solution { public static void main(String[] args) { Scanner sc = new Scanner(System.in); int n = sc.nextInt(); System.out.println(n + 1); } }\n",
    "c": "#include <stdio.h>\nint main() { int n; scanf(\"%d\", &n); printf(\"%d\\n\", n + 1); return 0; }\n",
    "cpp": "#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; cout << n + 1 << endl; return 0; }\n",
    "go": "package main\nimport (\"fmt\")\nfunc main() { var n int; fmt.Scan(&n); fmt.Println(n + 1) }\n",
    "rust": "use std::io;\nfn main() { let mut s = String::new(); io::stdin().read_line(&mut s).unwrap(); let n: i64 = s.trim().parse().unwrap(); println!(\"{}\", n + 1); }\n",
    "php": "<?php\n$n = (int)trim(fgets(STDIN));\necho $n + 1 . \"\\n\";\n",
    "ruby": "n = gets.to_i\nputs n + 1\n",
    "kotlin": "fun main() { val n = readLine()!!.trim().toInt(); println(n + 1) }\n",
    "swift": "let n = Int(readLine()!)!\nprint(n + 1)\n",  # deliberately wrong
}

_SQUARE_COMPILE_ERROR = {
    "csharp": "this is not valid C# {{{",
    "java": "this is not valid java {{{",
    "c": "int main( { this is not valid C",
    "cpp": "int main( { this is not valid C++",
    "go": "package main\nfunc main() { this is not valid go",
    "rust": "fn main() { this is not valid rust !!!",
    "kotlin": "fun main() { this is not valid kotlin {{{",
    "typescript": "let x: string[ = 5;",
    "swift": "func main( { this is not valid swift",
}

_SQUARE_RUNTIME_ERROR = {
    "python": "n = int(input())\nraise ValueError('boom')",
    "javascript": "throw new Error('boom');",
    "csharp": "using System;\nclass Program { static void Main() { throw new Exception(\"boom\"); } }\n",
    "java": "public class Solution { public static void main(String[] args) { throw new RuntimeException(\"boom\"); } }\n",
    "c": "int main() { int *p = 0; return *p; }\n",
    "cpp": "int main() { int *p = 0; return *p; }\n",
    "go": "package main\nfunc main() { panic(\"boom\") }\n",
    "rust": "fn main() { panic!(\"boom\"); }\n",
    "php": "<?php\nthrow new Exception('boom');\n",
    "ruby": "raise 'boom'\n",
    "kotlin": "fun main() { throw RuntimeException(\"boom\") }\n",
    "swift": "fatalError(\"boom\")\n",
}

_TIMEOUT_CODE = {
    "python": "while True:\n    pass\n",
    "javascript": "while(true) {}",
    "csharp": "using System;\nclass Program { static void Main() { while(true) {} } }\n",
    "java": "public class Solution { public static void main(String[] args) { while(true) {} } }\n",
    "c": "int main() { while(1) {} return 0; }\n",
    "cpp": "int main() { while(1) {} return 0; }\n",
    "go": "package main\nfunc main() { for { } }\n",
    "rust": "fn main() { loop {} }\n",
    "php": "<?php\nwhile(true) {}\n",
    "ruby": "while true do end\n",
    "kotlin": "fun main() { while (true) {} }\n",
    "swift": "while true {}\n",
}

# Step 8: stdin realistic assessment behavior -- sum of a list
_SUM_LIST_VALID = {
    "python": "n = int(input())\nnums = list(map(int, input().split()))\nprint(sum(nums))",
    "javascript": "const lines = require('fs').readFileSync(0, 'utf-8').trim().split('\\n');const nums = lines[1].split(' ').map(Number);console.log(nums.reduce((a,b)=>a+b,0));",
    "csharp": "using System;\nusing System.Linq;\nclass Program { static void Main() { Console.ReadLine(); var nums = Console.ReadLine().Split(' ').Select(int.Parse); Console.WriteLine(nums.Sum()); } }\n",
    "java": "import java.util.*;\npublic class Solution { public static void main(String[] args) { Scanner sc = new Scanner(System.in); int n = sc.nextInt(); int sum = 0; for (int i=0;i<n;i++) sum += sc.nextInt(); System.out.println(sum); } }\n",
    "c": "#include <stdio.h>\nint main() { int n; scanf(\"%d\", &n); int sum=0, x; for (int i=0;i<n;i++) { scanf(\"%d\", &x); sum += x; } printf(\"%d\\n\", sum); return 0; }\n",
    "cpp": "#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; int sum=0, x; for (int i=0;i<n;i++) { cin >> x; sum += x; } cout << sum << endl; return 0; }\n",
    "go": "package main\nimport \"fmt\"\nfunc main() { var n int; fmt.Scan(&n); sum := 0; for i := 0; i < n; i++ { var x int; fmt.Scan(&x); sum += x }; fmt.Println(sum) }\n",
    "rust": "use std::io::{self, Read};\nfn main() { let mut s = String::new(); io::stdin().read_to_string(&mut s).unwrap(); let mut it = s.split_whitespace().map(|x| x.parse::<i64>().unwrap()); let n = it.next().unwrap(); let sum: i64 = it.take(n as usize).sum(); println!(\"{}\", sum); }\n",
    "php": "<?php\nfgets(STDIN);\n$nums = array_map('intval', explode(' ', trim(fgets(STDIN))));\necho array_sum($nums) . \"\\n\";\n",
    "ruby": "gets\nnums = gets.split.map(&:to_i)\nputs nums.sum\n",
    "kotlin": "fun main() { readLine(); val nums = readLine()!!.trim().split(\" \").map { it.toInt() }; println(nums.sum()) }\n",
    "swift": "_ = readLine()\nlet nums = readLine()!.split(separator: \" \").map { Int($0)! }\nprint(nums.reduce(0, +))\n",
}


def _available_languages():
    return sorted(RUNNER_REGISTRY.keys())


def test_all_registered_runners_are_actually_available_right_now():
    # swift now runs through the official Linux swift Docker image —
    # the same sandbox every other language uses — registered exactly
    # like the other 12 (the old local-subprocess swift_runner.py,
    # blocked by a genuine Windows-toolchain gap, is still unused).
    assert "swift" in RUNNER_REGISTRY

    for lang, runner in RUNNER_REGISTRY.items():
        assert runner.available(), f"{lang} runner registered but reports unavailable"


def test_step6_same_problem_all_languages_return_25():
    results = {}
    for lang in _available_languages():
        code = _SQUARE_VALID.get(lang)
        if code is None:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, stdin="5")
        )
        results[lang] = result
        assert result.success is True, f"{lang} failed: {result.stderr[:300]}"
        assert result.stdout.strip() == "25", f"{lang} produced {result.stdout!r}"

    # every language this test HAS a solution for must have actually run
    assert set(results.keys()) == set(_SQUARE_VALID.keys()) & set(_available_languages())
    return results


def test_step7_wrong_answer_executes_successfully_but_produces_wrong_output():
    for lang, code in _SQUARE_WRONG_ANSWER.items():
        if lang not in RUNNER_REGISTRY:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, stdin="5")
        )
        assert result.success is True, f"{lang}: {result.stderr[:200]}"
        assert result.stdout.strip() != "25"  # a real WRONG_ANSWER is a scoring-layer judgment


def test_step7_compile_errors_classified_correctly():
    for lang, code in _SQUARE_COMPILE_ERROR.items():
        if lang not in RUNNER_REGISTRY:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, stdin="5")
        )
        assert result.success is False
        assert result.error_type == "compile_error", f"{lang}: got {result.error_type}"


def test_step7_runtime_errors_classified_correctly():
    for lang, code in _SQUARE_RUNTIME_ERROR.items():
        if lang not in RUNNER_REGISTRY:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, stdin="5")
        )
        assert result.success is False
        assert result.error_type == "runtime_error", f"{lang}: got {result.error_type}"


def test_step7_timeouts_classified_correctly():
    for lang, code in _TIMEOUT_CODE.items():
        if lang not in RUNNER_REGISTRY:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, timeout_ms=1500)
        )
        assert result.success is False, f"{lang}: expected timeout, got success"
        assert result.error_type == "timeout", f"{lang}: got {result.error_type}"
        assert result.timed_out is True


def test_step8_realistic_stdin_stdout_sum_of_list():
    stdin = "5\n1 2 3 4 5"
    for lang, code in _SUM_LIST_VALID.items():
        if lang not in RUNNER_REGISTRY:
            continue
        result = RUNNER_REGISTRY[lang].execute(
            ExecutionRequest(language=lang, source_code=code, stdin=stdin)
        )
        assert result.success is True, f"{lang}: {result.stderr[:300]}"
        assert result.stdout.strip() == "15", f"{lang} produced {result.stdout!r}"
