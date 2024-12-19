import argparse
import logging
import subprocess
import json
import pathlib
import shutil
import os

parser = argparse.ArgumentParser()

parser.add_argument("DIR")
parser.add_argument("--verbose", action="store_true")

args = parser.parse_args()

level = logging.INFO
if args.verbose:
    level = logging.DEBUG
logging.basicConfig(level=level)

TOP_LEVEL_DIR = pathlib.Path(args.DIR)
assert TOP_LEVEL_DIR.exists() and TOP_LEVEL_DIR.is_dir()

FLAC_DIRECTORIES = set()
for flac_file in TOP_LEVEL_DIR.glob("**/*.flac"):
    FLAC_DIRECTORIES.add(flac_file.parent)

logging.debug(f"Found {len(FLAC_DIRECTORIES)} directories containing .flac files")

for flac_directory in FLAC_DIRECTORIES:
    flac_files = list(flac_directory.glob("*.flac"))
    for f in flac_files:
        logging.debug(f"- {f}")

    logging.debug("Running ffmpeg-normalize...")
    flac_files_str = [str(f) for f in flac_files]
    arg_list = ["ffmpeg-normalize", *flac_files_str, "-p", "-n", "-f", "-nt", "peak"]
    logging.debug(" ".join(arg_list))
    results = subprocess.run(arg_list, capture_output=True)
    results_json = json.loads(results.stdout.decode("utf-8"))

    logging.debug(results_json)

    max_peak = -100
    for record in results_json:
        max_peak = max(max_peak, record.get("max"))

    logging.info(f"Found max peak of {max_peak}")

    if max_peak > -1.5:
        logging.info(f"Max peak above -1.5dB floor, skipping")
        continue

    gain_to_add = round(abs(max_peak), 1) - 1
    logging.info(f"Applying +{gain_to_add}dB to each track")

    OUTPUT_DIR = pathlib.Path("/tmp/normalize/converted")
    FILE_MAPPING = {}

    for f in flac_files:
        f = pathlib.Path(f)
        out_file = OUTPUT_DIR / f.name

        volume_arg = f"volume={gain_to_add}dB"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(f),
            "-af", volume_arg,
            "-c:a", "flac", 
            str(out_file)
        ])

        FILE_MAPPING[out_file] = f

    if args.verbose:
        logging.info("Complete, running ffmpeg-normalize to show new results")

        out_files = [str(f) for f in OUTPUT_DIR.glob("*.flac")]
        results = subprocess.run(["ffmpeg-normalize", *out_files, "-p", "-n", "-f", "-nt", "peak"], capture_output=True)
        results_json = json.loads(results.stdout.decode("utf-8"))
        logging.info(results_json)

    for source, target in FILE_MAPPING.items():
        logging.debug(f"Moving {source} -> {target}")
        shutil.copyfile(source, target)
        os.remove(source)
