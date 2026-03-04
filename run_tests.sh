#!/bin/bash
#
# CockroachDB Operator VKS Test Runner Wrapper
#
# This script provides a convenient wrapper around test_runner.py
#
# Usage:
#   ./run_tests.sh --config config.yaml           # Run all tests with config
#   ./run_tests.sh --config config.yaml --test VKS-01  # Run single test
#   ./run_tests.sh --generate-config              # Generate sample config
#   ./run_tests.sh --help                         # Show help
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

# Check for PyYAML
if ! python3 -c "import yaml" 2>/dev/null; then
    echo "Installing required Python packages..."
    pip3 install -r requirements.txt --quiet
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

# Check for config file if running tests
CONFIG_FILE=""
for arg in "$@"; do
    if [[ "$arg" == "--config" ]] || [[ "$arg" == "-f" ]]; then
        CONFIG_NEXT=true
    elif [[ "$CONFIG_NEXT" == "true" ]]; then
        CONFIG_FILE="$arg"
        CONFIG_NEXT=false
    fi
done

# If config file specified, check it exists
if [[ -n "$CONFIG_FILE" ]] && [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: Configuration file not found: $CONFIG_FILE"
    echo "Run './run_tests.sh --generate-config' to create a sample configuration"
    exit 1
fi

# If no config specified and not a list/help/generate command, check for default
if [[ -z "$CONFIG_FILE" ]]; then
    case "$*" in
        *--list*|*--help*|*-h*|*--generate-config*|*--show-config*|*--validate-config*)
            # These commands don't need a config file
            ;;
        *)
            if [[ -f "config.yaml" ]]; then
                echo "Using default configuration: config.yaml"
            else
                echo "Warning: No configuration file specified."
                echo "Use --config <file> or create config.yaml"
                echo "Run './run_tests.sh --generate-config' to create a sample configuration"
            fi
            ;;
    esac
fi

# Run the test runner
python3 test_runner.py "$@"
