import pathlib
import argparse
from plexapi.myplex import MyPlexAccount
import logging
import os
import dotenv
import time

parser = argparse.ArgumentParser()

parser.add_argument("M3U_FILE")
parser.add_argument("--verbose", action="store_true")

args = parser.parse_args()

log_level = logging.INFO
if args.verbose:
    log_level = logging.DEBUG

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=log_level,
    datefmt='%Y-%m-%d %H:%M:%S'
)

M3U_FILE = pathlib.Path(args.M3U_FILE)

logging.info("Parsing M3U file")
m3u_paths = []
with open(M3U_FILE, "r", encoding="UTF8") as f:
    for line in f.readlines():
        SOURCE = os.getenv("LIBRARY_PATH_SOURCE", None)
        TARGET = os.getenv("LIBRARY_PATH_TARGET", None)

        mapped_path = str(pathlib.Path(line.strip()))
        if (SOURCE is not None) and (TARGET is not None):
            mapped_path = mapped_path.replace(SOURCE, TARGET).replace("\\", "/")

        m3u_paths.append(pathlib.PurePosixPath(mapped_path))

dotenv.load_dotenv()
logging.info("Connecting to Plex...")
account = MyPlexAccount(os.getenv("PLEX_ACCOUNT"), os.getenv("PLEX_PASSWORD"), token=os.getenv("PLEX_TOKEN"))
plex = account.resource(os.getenv("PLEX_RESOURCE")).connect()

GUID_MAPPING = {}

start_time = time.time()
logging.info("Creating playlist mapping")
for track in plex.library.section(os.getenv("PLEX_LIBRARY")).all(libtype="track"):
    path = pathlib.PurePosixPath(track.media[0].parts[0].file)
    GUID_MAPPING[path] = track

end_time = time.time()
logging.info(f"Done in {end_time - start_time}s")

playlist_title = M3U_FILE.stem

try:
    plex.playlist(playlist_title).delete()
    print("Removed existing playlist")
except Exception as e:
    pass

logging.debug(GUID_MAPPING)

plex_track_ids = []
for file in m3u_paths:
    track = GUID_MAPPING.get(file)
    if track is None:
        logging.warning(f"Couldn't map {file}")
        continue
    plex_track_ids.append(track)

logging.debug(plex_track_ids)

logging.info(f"Adding {len(plex_track_ids)} tracks to {playlist_title}")

plex.createPlaylist(playlist_title, items=plex_track_ids)

logging.info(f"Added {len(plex_track_ids)} tracks to {playlist_title}")
