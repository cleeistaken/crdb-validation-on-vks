#!/bin/bash
#
# CockroachDB Operator VKS Test Runner Wrapper
#
# This script provides a convenient wrapper around test_runner.py
#
# Usage:
#   ./run_tests.sh                    # Run all tests
#   ./run_tests.sh --test VKS-01      # Run single test
#   ./run_tests.sh --help             # Show help
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required but not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "Error: Python $REQUIRED_VERSION or higher is required (found $PYTHON_VERSION)"
    exit 1
fi

# Check kubectl
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is required but not installed"
    exit 1
fi

# Check helm
if ! command -v helm &> /dev/null; then
    echo "Error: helm is required but not installed"
    exit 1
fi

# Set default kubeconfig if not set
if [ -z "$KUBECONFIG" ] && [ -f "vks-kubeconfig.yaml" ]; then
    export KUBECONFIG="$SCRIPT_DIR/vks-kubeconfig.yaml"
    echo "Using kubeconfig: $KUBECONFIG"
fi

# Run the test runner
python3 test_runner.py "$@"
