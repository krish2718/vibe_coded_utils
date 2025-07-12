#!/usr/bin/env python3

import serial
import time
import sys
import re
import argparse

# Platform-specific addresses
PLATFORM_CONFIGS = {
    "nrf5340": {
        "base": 0x50001000,  # Updated base address
        "enable_offset": 0x50C,
        "inst_hit_offset": 0x400,
        "inst_miss_offset": 0x404,
        "data_hit_offset": 0x408,
        "data_miss_offset": 0x40C,
        "has_inst": True,
        "has_data": True,
    },
    "nrf7002": {  # Use the same as nrf5340
        "base": 0x50001000,
        "enable_offset": 0x50C,
        "inst_hit_offset": 0x400,
        "inst_miss_offset": 0x404,
        "data_hit_offset": 0x408,
        "data_miss_offset": 0x40C,
        "has_inst": True,
        "has_data": True,
    },
    "nrf54l15": {
        "base": 0xE0082000,
        "enable_offset": 0x414,  # PROFILING.ENABLE
        "clear_offset": 0x418,  # PROFILING.CLEAR
        "hit_offset": 0x41C,  # PROFILING.HIT
        "miss_offset": 0x420,  # PROFILING.MISS
        "lmiss_offset": 0x424,  # PROFILING.LMISS
        "has_inst": False,
        "has_data": False,
        # No need for has_hit/has_miss/has_lmiss, just dump hit/miss
    },
}

SERIAL_PORT = "/dev/ttyACM1"
BAUDRATE = 115200
TIMEOUT = 5
PROMPT = "uart:~$"
DEBUG = False


def debug_print(message):
    """Print debug message only if DEBUG is True."""
    if DEBUG:
        print(f"DEBUG: {message}")


def send_command(ser, command):
    """Send a command and return the response."""
    ser.reset_input_buffer()  # Flush input buffer before sending
    debug_print(f"Sending: {command}")
    ser.write(f"{command}\r".encode("utf-8"))
    ser.flush()
    time.sleep(0.5)

    # Read response
    response = ""
    start_time = time.time()
    command_echoed = False

    while time.time() - start_time < TIMEOUT:
        if ser.in_waiting:
            line = ser.readline().decode(errors="replace").strip()
            if line:
                debug_print(f"Received: '{line}'")
                # Skip the command echo (first line that matches our command)
                if not command_echoed and command in line:
                    command_echoed = True
                    debug_print("Skipping command echo")
                    continue
                response += line + "\n"
                # Stop when we see the prompt or a result
                if PROMPT in line or "Read value" in line or "Writing value" in line:
                    break
        else:
            time.sleep(0.1)
    debug_print(f"Final response: {response}")
    return response


def parse_devmem_value(response):
    """Parse the hex value from devmem response."""
    match = re.search(r"Read value (0x[0-9a-fA-F]+)", response)
    if match:
        hex_value = match.group(1)
        # Always return int, even if 0
        return int(hex_value, 16)
    # If we see 'Read value 0x0' explicitly, return 0
    if "Read value 0x0" in response:
        return 0
    return None


def execute_devmem(ser, address, value=None, width=32):
    """Execute devmem command and return the parsed value (for reads) or success (for writes)."""
    if value is not None:
        command = f"devmem {address} {width} {value}"
    else:
        command = f"devmem {address} {width}"

    debug_print(f"execute_devmem called with address={address}, value={value}")
    response = send_command(ser, command)
    if value is not None:
        # For writes, treat 'Writing value', prompt, or empty as success
        if "Writing value" in response or PROMPT in response or response.strip() == "":
            return True
        return False
    else:
        # If we get only the prompt and no 'Read value', warn in debug
        if "Read value" not in response and PROMPT in response:
            debug_print(f"WARNING: No value returned for {address}, got only prompt.")
        return parse_devmem_value(response)


def enable_cache_profiling(ser, enable_reg):
    """Enable cache profiling and verify by reading back."""
    print("Enabling cache profiling...")
    result = execute_devmem(ser, enable_reg, value=1, width=32)
    if result:
        time.sleep(1)  # Wait before readback
        readback_raw = send_command(ser, f"devmem {enable_reg} 32")
        debug_print(f"Enable readback raw: {readback_raw}")
        readback = parse_devmem_value(readback_raw)
        if readback == 1:
            print("✓ Cache profiling enabled")
        else:
            print(f"✗ Failed to enable cache profiling (readback: {readback})")
    else:
        print("✗ Failed to enable cache profiling (write failed)")


def disable_cache_profiling(ser, enable_reg):
    """Disable cache profiling and verify by reading back."""
    print("Disabling cache profiling...")
    result = execute_devmem(ser, enable_reg, value=0, width=32)
    if result:
        time.sleep(1)  # Wait before readback
        readback_raw = send_command(ser, f"devmem {enable_reg} 32")
        debug_print(f"Disable readback raw: {readback_raw}")
        readback = parse_devmem_value(readback_raw)
        if readback == 0:
            print("✓ Cache profiling disabled")
        else:
            print(f"✗ Failed to disable cache profiling (readback: {readback})")
    else:
        print("✗ Failed to disable cache profiling (write failed)")


def check_cache_profiling_enabled(ser, enable_reg):
    """Check if cache profiling is enabled."""
    debug_print(f"Checking if cache profiling is enabled at {enable_reg}")
    result = execute_devmem(ser, enable_reg, value=None, width=32)
    if result is not None:
        enabled = result != 0
        debug_print(f"Cache profiling enabled: {enabled} (value: {result})")
        return enabled
    else:
        debug_print("Could not read cache profiling enable register")
        return False


def calculate_hit_rate(hits, misses):
    """Calculate hit rate percentage."""
    total = hits + misses
    if total == 0:
        return 0.0
    return (hits / total) * 100.0


def read_cache_counters(ser, platform_config):
    """Read all cache counters and display a nice summary."""
    print("Reading cache counters...")

    # Check if cache profiling is enabled
    enable_reg = platform_config["base"] + platform_config["enable_offset"]
    if not check_cache_profiling_enabled(ser, f"0x{enable_reg:08x}"):
        print("✗ Cache profiling is not enabled!")
        print("Please enable cache profiling first:")
        print("  python3 cache_profiler_serial.py nrf5340 enable")
        return {}

    print("✓ Cache profiling is enabled")

    counters = {}
    missing = []

    # nrf54l15: just dump hit/miss/lmiss
    if "nrf54l15" in platform_config.get("name", "nrf54l15") or (
        not platform_config.get("has_inst", False)
        and not platform_config.get("has_data", False)
    ):
        # Optionally clear counters before reading (uncomment if needed)
        # clear_reg = platform_config["base"] + platform_config["clear_offset"]
        # execute_devmem(ser, f"0x{clear_reg:08x}", value=1, width=32)
        # time.sleep(0.1)
        hit_reg = platform_config["base"] + platform_config["hit_offset"]
        miss_reg = platform_config["base"] + platform_config["miss_offset"]
        lmiss_offset = platform_config.get("lmiss_offset")
        hit_val = execute_devmem(ser, f"0x{hit_reg:08x}")
        miss_val = execute_devmem(ser, f"0x{miss_reg:08x}")
        lmiss_val = None
        if lmiss_offset is not None:
            lmiss_reg = platform_config["base"] + lmiss_offset
            lmiss_val = execute_devmem(ser, f"0x{lmiss_reg:08x}")
        if hit_val is not None:
            counters["hit"] = hit_val
        else:
            missing.append("hit")
        if miss_val is not None:
            counters["miss"] = miss_val
        else:
            missing.append("miss")
        if lmiss_val is not None:
            counters["line_miss"] = lmiss_val
        elif lmiss_offset is not None:
            missing.append("line_miss")
    else:
        # legacy style (nrf5340/nrf7002)
        if platform_config["has_inst"]:
            inst_hit_reg = platform_config["base"] + platform_config["inst_hit_offset"]
            inst_miss_reg = (
                platform_config["base"] + platform_config["inst_miss_offset"]
            )

            inst_hit = execute_devmem(ser, f"0x{inst_hit_reg:08x}")
            inst_miss = execute_devmem(ser, f"0x{inst_miss_reg:08x}")

            if inst_hit is not None:
                counters["instruction_hit"] = inst_hit
            else:
                missing.append("instruction_hit")
            if inst_miss is not None:
                counters["instruction_miss"] = inst_miss
            else:
                missing.append("instruction_miss")

        if platform_config["has_data"]:
            data_hit_reg = platform_config["base"] + platform_config["data_hit_offset"]
            data_miss_reg = (
                platform_config["base"] + platform_config["data_miss_offset"]
            )

            data_hit = execute_devmem(ser, f"0x{data_hit_reg:08x}")
            data_miss = execute_devmem(ser, f"0x{data_miss_reg:08x}")

            if data_hit is not None:
                counters["data_hit"] = data_hit
            else:
                missing.append("data_hit")
            if data_miss is not None:
                counters["data_miss"] = data_miss
            else:
                missing.append("data_miss")

    # Display nice summary
    print("\n" + "=" * 50)
    print("CACHE PROFILING SUMMARY")
    print("=" * 50)

    # nrf54l15 summary (just dump hit/miss/lmiss)
    if "hit" in counters or "miss" in counters or "line_miss" in counters:
        hits = counters.get("hit", "N/A")
        misses = counters.get("miss", "N/A")
        lmisses = counters.get("line_miss", "N/A")
        print(f"Cache Region:")
        print(f"  Hits:         {hits}")
        print(f"  Misses:       {misses}")
        print(f"  Line Misses:  {lmisses}")
        if hits != "N/A" and misses != "N/A":
            total = hits + misses
            hit_rate = calculate_hit_rate(hits, misses)
            print(f"  Total:        {total:,}")
            print(f"  Hit Rate:     {hit_rate:.1f}%")
        else:
            print(f"  Total:        N/A")
            print(f"  Hit Rate:     N/A")
        print()
    else:
        # legacy summary
        # Show instruction cache if we have at least one counter
        if "instruction_hit" in counters or "instruction_miss" in counters:
            inst_hits = counters.get("instruction_hit", "N/A")
            inst_misses = counters.get("instruction_miss", "N/A")

            print(f"Instruction Cache:")
            print(f"  Hits:     {inst_hits}")
            print(f"  Misses:   {inst_misses}")

            if inst_hits != "N/A" and inst_misses != "N/A":
                inst_total = inst_hits + inst_misses
                inst_hit_rate = calculate_hit_rate(inst_hits, inst_misses)
                print(f"  Total:    {inst_total:,}")
                print(f"  Hit Rate: {inst_hit_rate:.1f}%")
            else:
                print(f"  Total:    N/A")
                print(f"  Hit Rate: N/A")
            print()
        else:
            print("Instruction Cache: Not available")
            print()

        # Show data cache if we have at least one counter
        if "data_hit" in counters or "data_miss" in counters:
            data_hits = counters.get("data_hit", "N/A")
            data_misses = counters.get("data_miss", "N/A")

            print(f"Data Cache:")
            print(f"  Hits:     {data_hits}")
            print(f"  Misses:   {data_misses}")

            if data_hits != "N/A" and data_misses != "N/A":
                data_total = data_hits + data_misses
                data_hit_rate = calculate_hit_rate(data_hits, data_misses)
                print(f"  Total:    {data_total:,}")
                print(f"  Hit Rate: {data_hit_rate:.1f}%")
            else:
                print(f"  Total:    N/A")
                print(f"  Hit Rate: N/A")
            print()
        else:
            print("Data Cache: Not available")
            print()

        # Show overall stats only if we have all counters
        if (
            "instruction_hit" in counters
            and "instruction_miss" in counters
            and "data_hit" in counters
            and "data_miss" in counters
        ):
            total_hits = counters["instruction_hit"] + counters["data_hit"]
            total_misses = counters["instruction_miss"] + counters["data_miss"]
            total_accesses = total_hits + total_misses
            overall_hit_rate = calculate_hit_rate(total_hits, total_misses)

            print(f"Overall Cache Performance:")
            print(f"  Total Hits:     {total_hits:,}")
            print(f"  Total Misses:   {total_misses:,}")
            print(f"  Total Accesses: {total_accesses:,}")
            print(f"  Overall Hit Rate: {overall_hit_rate:.1f}%")
        elif len(counters) > 0:
            print("Partial results shown above. Some counters not available.")
            if DEBUG and missing:
                print("Missing counters:", ", ".join(missing))
        else:
            print("No cache counters available")
            print(
                "Try enabling cache profiling first: python3 cache_profiler_serial.py nrf5340 enable"
            )

    print("=" * 50)

    return counters


def show_usage():
    """Show usage information."""
    print("Cache Profiler using Python serial")
    print("Usage: python3 cache_profiler_serial.py <platform> [command] [--debug]")
    print("")
    print("Platforms:")
    for platform in PLATFORM_CONFIGS.keys():
        print(f"  {platform}")
    print("")
    print("Commands:")
    print("  enable    - Enable cache profiling")
    print("  disable   - Disable cache profiling")
    print("  read      - Read cache counters")
    print("  stats     - Show cache statistics")
    print("")
    print("Options:")
    print("  --debug   - Enable debug output")
    print("")
    print("Examples:")
    print("  python3 cache_profiler_serial.py nrf5340 enable")
    print("  python3 cache_profiler_serial.py nrf5340 read")
    print("  python3 cache_profiler_serial.py nrf5340 read --debug")


def main():
    parser = argparse.ArgumentParser(description="Cache Profiler using Python serial")
    parser.add_argument("platform", help="Platform (nrf5340, nrf7002, nrf54l15)")
    parser.add_argument(
        "command", nargs="?", help="Command (enable, disable, read, stats)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")

    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    if args.platform not in PLATFORM_CONFIGS:
        print(f"Error: Platform '{args.platform}' not supported")
        print("Supported platforms:", list(PLATFORM_CONFIGS.keys()))
        return

    platform_config = PLATFORM_CONFIGS[args.platform]
    enable_reg = platform_config["base"] + platform_config["enable_offset"]
    # Add platform name for easier detection in read_cache_counters
    platform_config["name"] = args.platform

    debug_print("Script starting")
    print(f"Platform: {args.platform}")
    debug_print(f"Serial device: {SERIAL_PORT}")
    debug_print(f"Enable register: 0x{enable_reg:08x}")
    debug_print(f"Command: {args.command}")

    try:
        with serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1) as ser:
            # Flush buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.2)

            if args.command == "enable":
                debug_print("Calling enable_cache_profiling")
                enable_cache_profiling(ser, f"0x{enable_reg:08x}")
            elif args.command == "disable":
                disable_cache_profiling(ser, f"0x{enable_reg:08x}")
            elif args.command in ["read", "stats"]:
                read_cache_counters(ser, platform_config)
            else:
                print(f"Error: Unknown command '{args.command}'")
                show_usage()

    except serial.SerialException as e:
        print(f"Error: Could not open serial port {SERIAL_PORT}: {e}")
    except KeyboardInterrupt:
        print("\nInterrupted by user")


if __name__ == "__main__":
    main()
