#!/usr/bin/env bash
# ==============================================================================
# HORIZON: End-to-End Execution & Automated Report Generation Pipeline
# Master Orchestrator for Thesis Research Code (TU Bergakademie Freiberg)
# ==============================================================================

set -e

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${WORKSPACE_DIR}"

# Default parameters
MODEL_ARG="Kreuzzelg/qwen36-35b-a3b-colibri-i4-gs64"
RUN_MODE="normal" # normal, quick, full
SKIP_EVAL=0

print_banner() {
    echo "========================================================================"
    echo "  HORIZON: Mathematical Guardrails & Automated Report Pipeline"
    echo "  Author: Parsa (M.Sc. Data Science, TU Bergakademie Freiberg)"
    echo "========================================================================"
}

print_usage() {
    echo "Usage: ./run_all.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -m, --model MODEL_PATH    Path or identifier of local/HuggingFace model for analysis"
    echo "  -q, --quick               Quick mode: tests + figures + compile report with existing results"
    echo "  -f, --full                Full mode: run all benchmarks (Stage 1, AgentDojo, Jailbreaks) & report"
    echo "  --skip-tests              Skip pytest verification suite"
    echo "  -h, --help                Display this help message and exit"
    echo ""
    echo "Examples:"
    echo "  ./run_all.sh                                # Standard run: tests, figures, report generation"
    echo "  ./run_all.sh --model /path/to/downloaded    # Run synthesis with downloaded local model"
    echo "  ./run_all.sh --quick                        # Fast report compilation from current results"
    echo "  ./run_all.sh --full                         # Run full benchmark suites before report"
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)
            MODEL_ARG="$2"
            shift 2
            ;;
        -q|--quick)
            RUN_MODE="quick"
            shift
            ;;
        -f|--full)
            RUN_MODE="full"
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=1
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            print_usage
            exit 1
            ;;
    esac
done

print_banner

# Step 0: Ensure Python Virtual Environment is active
if [ -d ".venv" ]; then
    echo "[0/5] Activating virtual environment (.venv)..."
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "[ERROR] .venv virtual environment not found in ${WORKSPACE_DIR}!"
    exit 1
fi

export PYTHONPATH="${WORKSPACE_DIR}:${PYTHONPATH}"

# Step 1: Run Pytest Unit Verification Suite
if [ "${SKIP_TESTS:-0}" -eq 0 ]; then
    echo ""
    echo "[1/5] Running Pytest Verification Suite (Stage 1, Stage 2, Zero-Slide-Literal Checks)..."
    python -m pytest tests/ -q
else
    echo ""
    echo "[1/5] Skipping Pytest verification (--skip-tests)."
fi

# Step 2: Regenerate All Thesis Figures (F1–F3, F5–F7, F9, F12, F15, F16–F18)
echo ""
echo "[2/5] Generating All 12 Thesis Research Figures (PDF, PNG, CSV)..."
python scripts/make_figures.py

# Step 3: Run Full Benchmarks if requested or if results are missing
if [ "${RUN_MODE}" = "full" ]; then
    echo ""
    echo "[3/5] Running Stage 1 Paired Pilot Experiment..."
    python eval/stage1_runner.py --n-samples 50 --split fit

    echo ""
    echo "[3/5] Running AgentDojo Table 1 Ablation Study..."
    python eval/agentdojo_runner.py --n-tasks 20

    echo ""
    echo "[3/5] Running Direct Layer & Jailbreak Benchmark..."
    python eval/jailbreak_runner.py
elif [ ! -f "results/agentdojo_table1_results.csv" ] || [ ! -f "results/direct_layer_benchmark_results.csv" ]; then
    echo ""
    echo "[3/5] Results missing; running initial benchmark sweeps..."
    python eval/agentdojo_runner.py --n-tasks 10
    python eval/jailbreak_runner.py
else
    echo ""
    echo "[3/5] Existing empirical benchmark results detected in results/."
fi

# Step 4: Run Report Generator & AI Model Synthesis
echo ""
echo "[4/5] Executing LaTeX Report Generation & AI Model-Driven Synthesis..."
REPORT_CMD=(python scripts/generate_report.py)
if [ -n "${MODEL_ARG}" ]; then
    echo "      Model specified for analysis: ${MODEL_ARG}"
    REPORT_CMD+=(--model "${MODEL_ARG}")
fi

"${REPORT_CMD[@]}"

# Step 5: Verification & Summary
echo ""
echo "[5/5] Pipeline successfully completed!"
echo "========================================================================"
echo "Artifacts generated in report directory:"
echo "  - PDF Report:       ${WORKSPACE_DIR}/report/report.pdf"
echo "  - LaTeX Source:     ${WORKSPACE_DIR}/report/report.tex"
echo "  - Markdown Summary: ${WORKSPACE_DIR}/report/analysis_summary.md"
echo "  - Figures Directory: ${WORKSPACE_DIR}/report/figures/"
echo "========================================================================"
