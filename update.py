import pathlib
import mutagen
import argparse
from plexapi.myplex import MyPlexAccount
from mutagen.id3 import ID3, TextFrame
from mutagen.mp4 import MP4MetadataError
from attrs import define, field
from typing import List
from datetime import datetime
import logging
import os
import dotenv

parser = argparse.ArgumentParser()

parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--ratings", action="store_true")
parser.add_argument("--publisher", action="store_true")
parser.add_argument("--genre", action="store_true")
parser.add_argument("--year", action="store_true")
parser.add_argument("--track-metadata", action="store_true", help="artist, album, track number")
parser.add_argument("--track-genres", action="store_true")
parser.add_argument("--date-added", action="store_true")
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

def map_path(plex_path: pathlib.Path) -> pathlib.Path:
    SOURCE = os.getenv("LIBRARY_PATH_SOURCE", None)
    TARGET = os.getenv("LIBRARY_PATH_TARGET", None)

    mapped_path = str(plex_path)
    if (SOURCE is not None) and (TARGET is not None):
        mapped_path = mapped_path.replace(SOURCE, TARGET).replace("\\", "/")

    return pathlib.Path(mapped_path)

def extract_tag(file, tag_options: List[str]):
    for tag in tag_options:
        match = file.tags.get(tag, None)
        if match is not None:
            return match
    
    return None

def extract_publisher(file):
    result = extract_tag(file, ["publisher", "organization", "TPUB"])
    
    if result is None:
        return result
    
    if isinstance(result, list):
        result = result[0]
    
    result = str(result)

    result = result.split(";")[0].strip()
    
    return result

def extract_genres(file):
    result = extract_tag(file, ["genre", "TCON"])
    
    if result is None:
        return result
    
    if isinstance(result, str):
        result = [result]
    
    final_results = set()

    for r in result:
        r = r.lower()

        if ";" in r:
            for genre in r.split(";"):
                final_results.add(genre.strip())
        else:
            final_results.add(r)
    
    return list(final_results)

def extract_year(file):
    result = extract_tag(file, ["year", "YEAR", "DATE"])
    
    if result is None:
        return result
    
    if isinstance(result, list):
        result = result[0]
    
    return result

def extract_album(file):
    result = None

    if hasattr(file, "album"):
        result = file.album

    if result is None:
        result = extract_tag(file, ["album", "ALBUM", "TALB"])

    if result is None:
        return result
    
    if isinstance(result, list):
        result = result[0]
    
    return result

@define
class Rating:
    value: float = field()  # rating in stars (out of five)

    @staticmethod
    def from_plex(str):
        value = float(str) / 2.0
        assert value <= 5.0, f"{value} > 5.0"
        return Rating(value)

    @staticmethod
    def from_musicbee(str):
        value = float(str) / 20.0
        assert value <= 5.0, f"{value} > 5.0"
        return Rating(value)
    
    def to_plex(self):
        return str(self.value * 2.0)
    
    def to_musicbee(self):
        return str(self.value)


@define
class Track:
    path: str = field(default=None)
    rating: Rating = field(default=None)
    label: str = field(default=None)
    genres: List[str] = field(default=None)
    year: str = field(default=None)
    album: str = field(default=None)
    date_added: datetime = field(default=None)

    @staticmethod
    def from_file(path: str):
        t = Track()
        t.path = map_path(pathlib.Path(path))

        if not t.path.exists():
            logging.debug(f"Couldn't find {path}")
            return None

        t.date_added = datetime.fromtimestamp(os.path.getctime(t.path))

        file = None
        
        try:
            file = mutagen.File(t.path)
        except Exception as e:
            if isinstance(e, KeyboardInterrupt):
                raise e
            
            logging.error(f"Error processing {t.path}")
            logging.error(e)
            return None
        
        if t.path.suffix == ".m4a":
            # print(file.tags.keys())
            pass

        if file.get("rating", False):
            rating = file.get("rating")[0]
            t.rating = Rating.from_musicbee(rating)

        t.label = extract_publisher(file)

        t.genres = extract_genres(file)

        t.year = extract_year(file)

        t.album = extract_album(file)
        
        return t
    
    def write_to_file(self):
        pass

def get_current_file_rating(file: mutagen.File):
    result = file.get("rating")
    return result[0]

class Run:
    def __init__(self):
        self.actions = 0

    def update_file_rating(self, file: mutagen.File, new_rating: Rating):
        current_file_rating = file.get("rating")
        if current_file_rating is None:
            # unrated in MusicBee
            self.write_rating_to_file(file, new_rating)

            return

    def write_rating_to_file(self, file: mutagen.File, rating: Rating):
        transformed_rating = rating.to_musicbee()
        logging.debug(f"Writing {transformed_rating} ({rating}) to file")

        if args.dry_run:
            return
        
        if "rating" in file.tags:
            if isinstance(file.tags["rating"], list):
                file["rating"] = [transformed_rating]
            else:
                file["rating"] = TextFrame(encoding=3, text=transformed_rating)
        else:
            logging.debug("Ignoring since 'rating' not found in keys:")
            logging.debug(file.tags.keys())
            return

        file.save()

    def write_rating_to_plex_track(self, track, rating: Rating):
        logging.debug(f"Writing {rating} to {track}")
        self.actions += 1

        if args.dry_run:
            return

        track.userRating = rating.to_plex()

    def write_publisher_to_album(self, album, publisher: str):
        if album.studio == publisher:
            return

        self.actions += 1

        logging.debug(f"Writing record label '{publisher}' to {album}")

        if args.dry_run:
            return
        
        album.editStudio(publisher)

    def write_genres_to_entity(self, entity, incoming: List[str]):
        incoming_genres = incoming
        existing_genres = [g.tag for g in entity.genres]

        logging.debug(f"{existing_genres} => {incoming_genres}")

        adds = []
        removes = []

        # remove genres not in incoming list
        for g in existing_genres:
            if g.lower() not in [new_genre.lower() for new_genre in incoming_genres]:
                logging.info(f"Removing genre {g} from {entity}")
                self.actions += 1
                removes.append(g)
        
        # add new genres
        for g in incoming_genres:
            if g.lower() not in [entity_genre.lower() for entity_genre in existing_genres]:
                logging.info(f"Adding genre {g} to {entity}")
                self.actions += 1
                adds.append(g)
        
        if args.dry_run:
            return
        
        if adds:
            entity.addGenre(adds)
        
        if removes:
            entity.removeGenre(removes)
    
    def write_genres_to_album(self, album, incoming: List[str]):
        self.write_genres_to_entity(album, incoming)
    
    def write_genres_to_track(self, track, incoming: List[str]):
        self.write_genres_to_entity(track, incoming)

    def write_year_to_album(self, album, year: str):
        if len(year) < 4:
            if not args.dry_run and album.year:
                logging.debug(f"Removing year from {album}")
                album.year = ""
                album.originallyAvailableAt = None
                self.actions += 1
            return
        
        simple_year = year[:4]

        originally_available_at = None
        try:
            originally_available_at = datetime.strptime(year, "%Y-%m-%d")
        except ValueError:
            pass

        logging.debug(f"Writing year {year} to {album}")
        self.actions += 1

        if args.dry_run:
            return
            
        if originally_available_at is not None:
            if album.originallyAvailableAt != originally_available_at:
                album.originallyAvailableAt = originally_available_at

        if album.year != simple_year:
            album.year = simple_year

    def update_plex_track(self, plex_track, track: Track):
        if track.album == plex_track.parentTitle:
            return
        
        logging.debug(f"Updating {plex_track} album from '{plex_track.parentTitle}' to '{track.album}'")
        self.actions += 1

        if args.dry_run:
            return
        
        try:
            # do nothing
            # plex_track.parentKey = plex_track.artist().album(track.album).key
            pass
        except Exception as e:
            logging.error(f"Unable to update {plex_track}")
            logging.error(e)
            pass

    def sync_ratings(self, track):
        track_path = track.locations[0]

        if track.userRating is None:
            # can try to import it
            file = Track.from_file(track_path)
            if file is None:
                logging.debug(f"Skipping {track_path}")
                return

            if file.rating is None:
                # nothing to do
                return
            
            logging.debug(f"Processing {track_path}")

            self.write_rating_to_plex_track(track, file.rating)

            return
        else:
            # rated in Plex
            logging.debug(f"Processing {track_path}")
            file = mutagen.File(track_path)
            if file.get("rating") is None:
                # no local rating - update it
                self.update_file_rating(file, Rating.from_plex(track.userRating))
                return

            # we have a local rating - see if it is the same
            current_rating = Rating.from_musicbee(get_current_file_rating(file))

            transformed_rating = Rating.from_plex(track.userRating)

            if current_rating != transformed_rating:
                print(f"Rating conflict: {track_path}: file rating = {current_rating.value}, Plex rating = {transformed_rating.value}")
                response = input(f"Enter new rating:").strip()
                float_response = Rating(float(response))

                self.write_rating_to_file(file, float_response)
                self.write_rating_to_plex_track(track, float_response)

    def sync_publisher(self, album, first_track):
        track_path = first_track.locations[0]

        track = Track.from_file(track_path)
        if track is None:
            return

        if track.label is None:
            # nothing to do
            track.label = ""
        
        self.write_publisher_to_album(album, track.label)

    def sync_genre(self, album, first_track):
        track_path = first_track.locations[0]

        track = Track.from_file(track_path)
        if track is None:
            return

        if track.genres is None:
            # nothing to do
            return
        
        self.write_genres_to_album(album, track.genres)

    def sync_genre_track(self, track):
        track_path = track.locations[0]

        local_track = Track.from_file(track_path)
        if local_track is None:
            return
        
        if local_track.genres is None:
            # nothing to do
            return
        
        self.write_genres_to_track(track, local_track.genres)

    def sync_year(self, album, first_track):
        track_path = first_track.locations[0]

        track = Track.from_file(track_path)
        if track is None:
            return
        
        if track.year is None:
            track.year = ""
        
        self.write_year_to_album(album, track.year)

    def sync_album(self, album, track):
        track_path = track.locations[0]

        this_track = Track.from_file(track_path)
        if this_track is None:
            return
        
        if this_track.album is None:
            this_track.album = ""
        
        self.update_plex_track(track, this_track)

    def sync_date_added(self, album, track):
        track_path = track.locations[0]

        this_track = Track.from_file(track_path)
        if this_track is None:
            return
        
        logging.debug(f"Updating date added to {this_track.date_added}")
        self.actions += 1

        if args.dry_run:
            return
        
        try:
            album.editAddedAt(this_track.date_added)
            pass
        except Exception as e:
            logging.error(f"Unable to update {track_path}")
            logging.error(e)
            pass

def main():
    dotenv.load_dotenv()
    logging.info("Connecting...")
    account = MyPlexAccount(os.getenv("PLEX_ACCOUNT"), os.getenv("PLEX_PASSWORD"), token=os.getenv("PLEX_TOKEN"))
    plex = account.resource(os.getenv("PLEX_RESOURCE")).connect()
    logging.info("Connected")

    run = Run()

    album_count = 0
    for album in plex.library.section(os.getenv("PLEX_LIBRARY")).albums():
        album.reload()
        album_count += 1
        album_genres = [g.tag for g in album.genres]
        is_loose = "Loose" in album_genres
        only_need_first_track = not any((args.ratings, args.track_metadata, args.track_genres))

        logging.debug(f"Processing album {album}")

        original_actions = run.actions

        for i, track in enumerate(album.tracks(), start=0):
            track.reload()
            is_first_track = i == 0

            if is_first_track:
                # first track
                if args.publisher:
                    run.sync_publisher(album, track)
                
                if args.genre:
                    run.sync_genre(album, track)
                
                if args.year:
                    run.sync_year(album, track)

            if args.ratings:
                run.sync_ratings(track)
            
            if args.track_metadata:
                run.sync_album(album, track)
            
            if args.date_added:
                run.sync_date_added(album, track)
            
            if args.track_genres:
                run.sync_genre_track(track)
            
            if only_need_first_track:
                break
        
        if run.actions == original_actions:
            logging.debug("Nothing to do")

    logging.info(f"Processed {album_count} albums")
    logging.info(f"Performed {run.actions} actions")

if __name__ == "__main__":
    main()
