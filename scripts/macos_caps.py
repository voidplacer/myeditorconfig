#!/usr/bin/env python3
import json
import re
import subprocess


def main():
    output = subprocess.check_output(["/usr/bin/hidutil", "property", "--get", "UserKeyMapping"], text=True)
    mappings = []
    for block in re.findall(r"\{([^{}]*)\}", output, re.S):
        values = dict(re.findall(r"(HIDKeyboardModifierMapping(?:Src|Dst))\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;", block))
        if len(values) != 2:
            raise ValueError("Unrecognized hidutil mapping; refusing to replace it")
        mappings.append({key: int(value, 0) if value.startswith("0x") else int(value) for key, value in values.items()})
    if not mappings and output.strip() not in ("(null)", "null", "") and not re.fullmatch(r"\(\s*\)", output.strip()):
        raise ValueError("Unrecognized hidutil output")
    mappings = [item for item in mappings if item["HIDKeyboardModifierMappingSrc"] != 0x700000039]
    mappings.append({"HIDKeyboardModifierMappingSrc": 0x700000039, "HIDKeyboardModifierMappingDst": 0x700000029})
    subprocess.run(["/usr/bin/hidutil", "property", "--set", json.dumps({"UserKeyMapping": mappings})], check=True)


if __name__ == "__main__":
    main()
