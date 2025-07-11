#!/bin/bash

# Script to dump biggest structs in all object files using pahole
# Usage: ./dump_biggest_structs_robust.sh <build_directory> <size_threshold_bytes> [max_structs_per_file] [--debug] [--include pattern] [--exclude pattern] [--name-pattern pattern]

set -e

# Parse command line arguments
DEBUG_MODE=false
BUILD_DIR=""
SIZE_THRESHOLD=""
MAX_STRUCTS=""
INCLUDE_PATTERN=""
EXCLUDE_PATTERN=""
NAME_PATTERN=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
    --debug)
        DEBUG_MODE=true
        shift
        ;;
    --include)
        INCLUDE_PATTERN="$2"
        shift 2
        ;;
    --exclude)
        EXCLUDE_PATTERN="$2"
        shift 2
        ;;
    --name-pattern)
        NAME_PATTERN="$2"
        shift 2
        ;;
    *)
        if [ -z "$BUILD_DIR" ]; then
            BUILD_DIR="$1"
        elif [ -z "$SIZE_THRESHOLD" ]; then
            SIZE_THRESHOLD="$1"
        elif [ -z "$MAX_STRUCTS" ]; then
            MAX_STRUCTS="$1"
        else
            echo "Error: Too many arguments"
            echo "Usage: $0 <build_directory> <size_threshold_bytes> [max_structs_per_file] [--debug] [--include pattern] [--exclude pattern] [--name-pattern pattern]"
            echo ""
            echo "Examples:"
            echo "  $0 samples/wifi/softap/build_70dk 1024"
            echo "  $0 samples/bluetooth/build_52dk 512 10 --debug"
            echo "  $0 samples/wifi/softap/build_70dk 1024 --include wifi"
            echo "  $0 samples/wifi/softap/build_70dk 1024 --exclude test"
            echo "  $0 samples/wifi/softap/build_70dk 1024 --name-pattern 'wifi.*'"
            exit 1
        fi
        shift
        ;;
    esac
done

# Check if required arguments are provided
if [ -z "$BUILD_DIR" ] || [ -z "$SIZE_THRESHOLD" ]; then
    echo "Usage: $0 <build_directory> <size_threshold_bytes> [max_structs_per_file] [--debug] [--include pattern] [--exclude pattern] [--name-pattern pattern]"
    echo ""
    echo "Examples:"
    echo "  $0 samples/wifi/softap/build_70dk 1024"
    echo "  $0 samples/bluetooth/build_52dk 512 10 --debug"
    echo "  $0 samples/wifi/softap/build_70dk 1024 --include wifi"
    echo "  $0 samples/wifi/softap/build_70dk 1024 --exclude test"
    echo "  $0 samples/wifi/softap/build_70dk 1024 --name-pattern 'wifi.*'"
    echo ""
    echo "Filtering Options:"
    echo "  --include pattern    Only process files matching pattern"
    echo "  --exclude pattern    Skip files matching pattern"
    echo "  --name-pattern regex Only show structs matching regex pattern"
    exit 1
fi

# Set default for MAX_STRUCTS if not provided
if [ -z "$MAX_STRUCTS" ]; then
    MAX_STRUCTS="0" # Default to 0 (no limit) when using size threshold
fi

# Validate size threshold is a number
if ! [[ "$SIZE_THRESHOLD" =~ ^[0-9]+$ ]]; then
    echo "Error: Size threshold must be a positive integer"
    exit 1
fi

# Check if build directory exists
if [ ! -d "$BUILD_DIR" ]; then
    echo "Error: Build directory '$BUILD_DIR' does not exist"
    exit 1
fi

# Check if pahole is available
if ! command -v pahole &>/dev/null; then
    echo "Error: pahole is not installed. Please install it first."
    echo "On Ubuntu/Debian: sudo apt-get install pahole"
    echo "On CentOS/RHEL: sudo yum install pahole"
    exit 1
fi

if [ "$DEBUG_MODE" = true ]; then
    echo "Debug mode enabled"
fi

echo "Scanning build directory: $BUILD_DIR"
echo "Will show all structs larger than $SIZE_THRESHOLD bytes"
if [ "$MAX_STRUCTS" -gt 0 ]; then
    echo "Limited to $MAX_STRUCTS structs per file"
fi
if [ -n "$INCLUDE_PATTERN" ]; then
    echo "Include pattern: $INCLUDE_PATTERN"
fi
if [ -n "$EXCLUDE_PATTERN" ]; then
    echo "Exclude pattern: $EXCLUDE_PATTERN"
fi
if [ -n "$NAME_PATTERN" ]; then
    echo "Name pattern: $NAME_PATTERN"
fi
echo "=================================================="

# Function to extract struct sizes from pahole output
extract_struct_sizes() {
    local obj_file="$1"
    local temp_file=$(mktemp)

    # Run pahole and capture output
    pahole "$obj_file" 2>/dev/null >"$temp_file" || return 1

    # Parse the output to extract struct names and sizes
    awk -v threshold="$SIZE_THRESHOLD" -v debug="$DEBUG_MODE" '
    BEGIN {
        in_struct = 0;
        struct_name = "";
        struct_started = 0;
    }
    
    /^struct / {
        # Start of a new struct
        struct_name = $2;
        gsub(/;$/, "", struct_name);  # Remove trailing semicolon
        in_struct = 1;
        struct_started = 1;
        if (debug == "true") {
            printf "DEBUG: Found struct: %s\n", struct_name > "/dev/stderr";
        }
        next;
    }
    
    in_struct && /^[[:space:]]*\/\*[[:space:]]*size:[[:space:]]*[0-9]+/ {
        # Found size comment in format "/* size: X, ... */"
        if (struct_started) {
            size = $3;
            gsub(/,/, "", size);  # Remove trailing comma
            if (size >= threshold) {
                printf "%s %s\n", size, struct_name;
                if (debug == "true") {
                    printf "DEBUG: Struct %s size %s >= threshold %s\n", struct_name, size, threshold > "/dev/stderr";
                }
            } else if (debug == "true") {
                printf "DEBUG: Struct %s size %s < threshold %s (skipping)\n", struct_name, size, threshold > "/dev/stderr";
            }
            struct_started = 0;
        }
        next;
    }
    
    in_struct && /^[[:space:]]*\/\*[[:space:]]*[0-9]+[[:space:]]*bytes[[:space:]]*\*\/[[:space:]]*$/ {
        # Alternative size comment format "/* X bytes */"
        if (struct_started) {
            size = $2;
            if (size >= threshold) {
                printf "%s %s\n", size, struct_name;
                if (debug == "true") {
                    printf "DEBUG: Struct %s size %s >= threshold %s\n", struct_name, size, threshold > "/dev/stderr";
                }
            } else if (debug == "true") {
                printf "DEBUG: Struct %s size %s < threshold %s (skipping)\n", struct_name, size, threshold > "/dev/stderr";
            }
            struct_started = 0;
        }
        next;
    }
    
    in_struct && /^[[:space:]]*}[[:space:]]*$/ {
        # End of struct
        if (debug == "true" && struct_started) {
            printf "DEBUG: Struct %s ended without size info\n", struct_name > "/dev/stderr";
        }
        in_struct = 0;
        struct_name = "";
        struct_started = 0;
    }
    ' "$temp_file"

    rm -f "$temp_file"
}

# Function to get struct details
get_struct_details() {
    local obj_file="$1"
    local struct_name="$2"

    # Try to get detailed struct info
    pahole -C "$struct_name" "$obj_file" 2>/dev/null |
        sed 's/^/    /' |
        head -n 15
}

# Counter for processed files
processed_files=0
total_structs=0
total_files_with_structs=0
files_with_structs=()

# Find all .obj files in the build directory
while IFS= read -r obj_file; do
    if [ "$DEBUG_MODE" = true ]; then
        echo "DEBUG: Processing file: $obj_file" >&2
    fi

    # Apply include/exclude patterns
    if [ -n "$INCLUDE_PATTERN" ]; then
        if ! [[ "$obj_file" =~ $INCLUDE_PATTERN ]]; then
            if [ "$DEBUG_MODE" = true ]; then
                echo "DEBUG: Skipping $obj_file (does not match include pattern '$INCLUDE_PATTERN')" >&2
            fi
            continue
        fi
    fi
    if [ -n "$EXCLUDE_PATTERN" ]; then
        if [[ "$obj_file" =~ $EXCLUDE_PATTERN ]]; then
            if [ "$DEBUG_MODE" = true ]; then
                echo "DEBUG: Skipping $obj_file (matches exclude pattern '$EXCLUDE_PATTERN')" >&2
            fi
            continue
        fi
    fi

    # Extract struct sizes and filter by threshold
    if struct_sizes=$(extract_struct_sizes "$obj_file"); then
        if [ -n "$struct_sizes" ]; then
            struct_count=0
            file_output=""
            # Sort by size (descending) and apply limit if specified
            if [ "$MAX_STRUCTS" -gt 0 ]; then
                filtered_sizes=$(echo "$struct_sizes" | sort -nr | head -n "$MAX_STRUCTS")
            else
                filtered_sizes=$(echo "$struct_sizes" | sort -nr)
            fi

            while read -r size struct_name; do
                if [ -n "$size" ] && [ -n "$struct_name" ] && [ "$size" -ge "$SIZE_THRESHOLD" ]; then
                    # Apply name pattern filtering
                    if [ -n "$NAME_PATTERN" ]; then
                        if ! [[ "$struct_name" =~ $NAME_PATTERN ]]; then
                            if [ "$DEBUG_MODE" = true ]; then
                                echo "DEBUG: Skipping struct $struct_name (does not match name pattern '$NAME_PATTERN')" >&2
                            fi
                            continue
                        fi
                    fi

                    if [ "$struct_count" -eq 0 ]; then
                        file_output="File: $obj_file\n----------------------------------------\n"
                    fi
                    file_output+="  $struct_name: $size bytes\n"
                    file_output+="$(get_struct_details "$obj_file" "$struct_name")\n\n"
                    struct_count=$((struct_count + 1))
                fi
            done <<<"$filtered_sizes"

            if [ "$struct_count" -gt 0 ]; then
                printf "%b" "$file_output"
                total_structs=$((total_structs + struct_count))
                total_files_with_structs=$((total_files_with_structs + 1))
                files_with_structs+=("$obj_file")
            fi
        else
            if [ "$DEBUG_MODE" = true ]; then
                echo "DEBUG: No structs found larger than $SIZE_THRESHOLD bytes in $obj_file" >&2
            fi
        fi
    else
        if [ "$DEBUG_MODE" = true ]; then
            echo "DEBUG: pahole failed to analyze $obj_file" >&2
        fi
    fi

    processed_files=$((processed_files + 1))

    # Progress indicator every 10 files (only in debug mode or if verbose)
    if [ "$DEBUG_MODE" = true ] && [ $((processed_files % 10)) -eq 0 ]; then
        echo "DEBUG: Processed $processed_files files..." >&2
    fi

done < <(find "$BUILD_DIR" -name "*.obj" -type f)

echo ""
echo "=================================================="
echo "Analysis complete!"
echo "Processed $processed_files object files"
echo "Found structs >= $SIZE_THRESHOLD bytes in $total_files_with_structs files"
echo "Total structs found: $total_structs"

if [ "$DEBUG_MODE" = true ] && [ ${#files_with_structs[@]} -gt 0 ]; then
    echo ""
    echo "DEBUG: Files with matching structs:"
    for file in "${files_with_structs[@]}"; do
        echo "  $file"
    done
fi
