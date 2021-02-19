import json
import os
import re
import sys
import traceback
from enum import Enum
from string import Template
from typing import Dict, Any, Optional, List, TypeVar, Callable, Tuple, Pattern, Match, Set, TextIO

import requests

api_url: str = 'http://www.omdbapi.com/'
api_key: str = 'fb47edfb'

folder_name_template: Template = Template('${title} (${countries} ${year}, ${director} - ${actors})')

dirname_parsing_regex: Pattern = re.compile('(.*)\\(.*(\\d{4}).*\\)')
optional_title_parsing_regex: Pattern = re.compile('(.*),\\s+(the)', flags=re.IGNORECASE)

title_writing_regex: Pattern = re.compile('(the)\\s+(.*)', flags=re.IGNORECASE)

allowed_punctuation: Set[str] = {'¿' '?', '¡', '!', '.', ',', '\''}

ParserInputType: TypeVar = TypeVar('ParserInputType')
ParserOutputType: TypeVar = TypeVar('ParserOutputType')


def log(line: str, error: bool = False, silent: bool = False) -> None:
	output_file: TextIO = sys.stdout  # sys.stdout if not error else sys.stderr

	if not silent:
		print(line, file=output_file, flush=True)


def apply_safe_parse(
		unsafe_parser: Callable[[ParserInputType], ParserOutputType], raw_value: ParserInputType,
		default_value: Optional[ParserOutputType] = None
) -> Optional[ParserOutputType]:
	return safe_parse(unsafe_parser, default_value)(raw_value)


def safe_parse(
		unsafe_parser: Callable[[ParserInputType], ParserOutputType],
		default_value: Optional[ParserOutputType] = None
) -> Callable[[ParserInputType], Optional[ParserOutputType]]:
	def patched_parser(raw_value: Optional[ParserInputType]) -> ParserOutputType:
		if raw_value is None:
			return default_value
		# noinspection PyBroadException
		try:
			return unsafe_parser(raw_value)
		except Exception:
			return default_value

	return patched_parser


def parse_bool(raw_value: str) -> Optional[bool]:
	translation: Dict[str, bool] = {'true': True, 'false': False}
	return translation.get(raw_value.lower())


def extract_response(parsed_json: Dict[str, Any]) -> bool:
	return parse_bool(parsed_json['Response'])


class MovieConfig:
	MOVIE_CONFIG_FILENAME: str = 'config.json'

	ID_FIELD: str = 'imdbID'
	TITLE_FIELD: str = 'Title'

	imdb_id: str
	title: Optional[str]

	def __init__(self, imdb_id: str, title: Optional[str] = None):
		self.imdb_id = imdb_id
		self.title = title

	@classmethod
	def from_json(cls, parsed_json: Dict[str, Any]) -> 'MovieConfig':
		imdb_id: Optional[str] = parsed_json.get(cls.ID_FIELD)
		title: Optional[str] = parsed_json.get(cls.TITLE_FIELD)

		return cls(
			imdb_id,
			title
		)

	def write_to_file(self, path: str) -> None:
		json_contents: Dict[str, Any] = {
			type(self).ID_FIELD: self.imdb_id,
			type(self).TITLE_FIELD: self.title
		}

		with open(path, mode='w') as open_file:
			json.dump(json_contents, open_file)


class Movie:
	TITLE_FIELD: str = 'Title'
	YEAR_FIELD: str = 'Year'
	ID_FIELD: str = 'imdbID'

	ACTORS_FIELD: str = 'Actors'
	DIRECTOR_FIELD: str = 'Director'
	COUNTRY_FIELD: str = 'Country'

	title: str
	year: int
	imdb_id: str
	_raw_actors: Optional[List[str]]
	actors: Optional[str]
	director: Optional[str]
	_raw_countries: Optional[List[str]]
	countries: Optional[str]

	def __init__(
			self,
			title: str, year: int, imdb_id: str,
			actors: Optional[List[str]] = None, director: Optional[str] = None, countries: Optional[str] = None
	):
		self.set_title(title)
		self.year = year
		self.imdb_id = imdb_id
		self.set_actors(actors)
		self.director = cleanse_string(director, wildcard=' - ')
		self.set_countries(countries)

	@staticmethod
	def _shorten_countries(original_countries: List[str]) -> List[str]:
		db: CountryDb = CountryDb.get_singleton()

		transformed_countries: List[str] = []
		for original_country in original_countries:
			found_entry: Optional[CountryEntry] = db.find_anywhere(original_country)
			found_shortened: Optional[str] = found_entry.get_shorter_abbrev() if found_entry is not None else None

			if found_shortened is None:
				transformed_countries.append(original_country)
			else:
				transformed_countries.append(found_shortened)

		return transformed_countries

	def to_formatted_filename(self) -> str:
		return folder_name_template.safe_substitute(self.__dict__)

	def set_title(self, title: str) -> None:
		title_match: Match = title_writing_regex.match(title)

		if title_match is not None:
			first_part: str = title_match.group(2)
			second_part: str = title_match.group(1)

			title = first_part + ', ' + second_part

		self.title = cleanse_string(title)

	def set_actors(self, actors: Optional[List[str]]) -> None:
		self._raw_actors = actors
		self.actors = cleanse_string(self._calculate_actors())

	def set_countries(self, countries: Optional[str]) -> None:
		self._raw_countries = type(self)._shorten_countries(list(map(str.strip, countries.split(',')))) \
			if countries is not None else None
		self.countries = cleanse_string(self._calculate_countries(), wildcard='-')

	def _calculate_actors(self) -> Optional[str]:
		if self._raw_actors is None:
			return None

		return ', '.join(self._raw_actors)

	def _calculate_countries(self) -> Optional[str]:
		if self._raw_countries is None:
			return None

		return ', '.join(self._raw_countries)

	@classmethod
	def from_filename(cls, filename: str) -> Optional['Movie']:
		try:
			title, year = extract_info_from_dirname(filename)

			basic_movie_info: Dict[str, Any] = search_for_title(title, year)
			if basic_movie_info is None:
				return None
			rv: 'Movie' = cls.from_json(basic_movie_info)
			movie_details: Dict[str, Any] = get_movie_details(rv.imdb_id)
			rv.expand_details_from_json(movie_details)

			return rv
		except Exception as e:
			log("ERROR building Movie: {!r}".format(e))
			return None

	@classmethod
	def from_json(cls, parsed_json: Dict[str, Any]) -> 'Movie':
		title: Optional[str] = parsed_json.get(cls.TITLE_FIELD)
		year: Optional[int] = apply_safe_parse(int, parsed_json.get(cls.YEAR_FIELD))
		imdb_id: Optional[str] = parsed_json.get(cls.ID_FIELD)

		return cls(title, year, imdb_id)

	@classmethod
	def from_imdb_id(cls, imdb_id: str) -> Optional['Movie']:
		parsed_json: Optional[Dict[str, Any]] = get_movie_details(imdb_id)
		if parsed_json is None:
			return None

		movie: Movie = cls.from_json(parsed_json)
		movie.expand_details_from_json(parsed_json)

		return movie

	def expand_details_from_json(self, parsed_json: Dict[str, Any]) -> None:
		actors: Optional[List[str]] = apply_safe_parse(
			type(self)._extract_actors_list, parsed_json.get(type(self).ACTORS_FIELD)
		)
		director: Optional[str] = parsed_json.get(type(self).DIRECTOR_FIELD)
		country: Optional[str] = parsed_json.get(type(self).COUNTRY_FIELD)

		self._add_details(actors, director, country)

	def _add_details(self, actors: List[str], director: str, countries: str) -> None:
		self.set_actors(actors)
		self.director = cleanse_string(director, wildcard=' - ')
		self.set_countries(countries)

	@staticmethod
	def _extract_actors_list(raw_actors: str) -> List[str]:
		actors: List[str] = list(map(lambda s: s.strip(), raw_actors.split(',')))
		return actors

	def __repr__(self) -> str:
		return "<{} {}>".format(
			type(self),
			", ".join([
				"{}: ({})".format(key, value)
				for key, value in self.__dict__.items()
			])
		)


def prompt(
		prompt_text: str, reject_text: str, parser: Callable[[str], Tuple[bool, Optional[Any]]]
) -> Optional[Any]:
	accepted: bool = False
	parsed_value: Optional[Any] = None

	while not accepted:
		print(prompt_text, end='')
		raw_input: str = input()
		# noinspection PyBroadException
		try:
			accepted, parsed_value = parser(raw_input)
		except Exception:
			accepted = False
			parsed_value = None

		if not accepted:
			print(reject_text)

	return parsed_value


def prompt_imdb_id() -> 'Movie':
	def _movie_constructor(imdb_id: str) -> Tuple[bool, Optional[Movie]]:
		imdb_id = imdb_id.strip()
		if len(imdb_id) == 0:
			return True, None
		parsed_json: Optional[Dict[str, Any]] = get_movie_details(imdb_id)
		if parsed_json is None:
			return False, None

		parsed_movie: Movie = Movie.from_json(parsed_json)
		parsed_movie.expand_details_from_json(parsed_json)

		return True, parsed_movie

	movie: Optional[Movie] = prompt(
		"Input IMDB ID manually, or blank to skip: ",
		"Invalid IMDB ID, please try again",
		lambda imdb_id: apply_safe_parse(_movie_constructor, imdb_id, (False, None))
	)

	return movie


def prompt_title(default_title: str) -> Optional[str]:
	return prompt(
		"Input a movie title manually, or blank for default ({}): ".format(default_title),
		"Title is not valid, please try again",
		lambda input_title: (True, input_title.strip()) if len(input_title.strip()) > 0 else (True, default_title)
	)


def cleanse_string(title: str, replace_to_wildcard: bool = True, wildcard: str = '-') -> str:
	if title is None:
		return ''

	title = title.strip()
	clean_title_chars: List[str] = []
	for c in title:
		if c.isalnum() or c.isspace() or c in allowed_punctuation:
			clean_title_chars.append(c)
		else:
			if replace_to_wildcard:
				clean_title_chars.append(wildcard)

	clean_title: str = ''.join(clean_title_chars)
	clean_title = ' '.join(clean_title.split())

	return clean_title


def extract_info_from_dirname(dirname: str, silent=False) -> Tuple[str, Optional[int]]:
	log("Parsing title: {}".format(dirname), silent=silent)
	dirname_match: Match = dirname_parsing_regex.match(dirname)

	if dirname_match is None:
		log("Could not extract title, using whole dirname", error=True, silent=silent)
		title: str = dirname
		year: Optional[int] = None

	else:
		title: str = dirname_match.group(1).strip()
		log("Extracted title: {}".format(title), silent=silent)
		year: int = apply_safe_parse(int, dirname_match.group(2))
		log("Extracted year: {}".format(year), silent=silent)

	optional_match: Match = optional_title_parsing_regex.match(title)

	if optional_match is not None:
		first_part: str = optional_match.group(2)
		second_part: str = optional_match.group(1)

		title = first_part + ' ' + second_part

	title = cleanse_string(title, replace_to_wildcard=False)
	log("Cleansed title: {}".format(title), silent=silent)

	return title, year


def search_for_title(title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
	params: Dict[str, Any] = {
		'apikey': api_key,
		's': title,
		'r': 'json',
		'type': 'movie'
	}

	if year is not None:
		params['y'] = year

	full_url: str = api_url

	res: requests.Response = requests.get(
		full_url,
		params=params
	)

	search_results: Dict[str, Any] = res.json()

	result: Optional[bool] = apply_safe_parse(parse_bool, search_results.get('Response'))

	if not result:
		return None

	movie_list: List[Dict[str, Any]] = search_results['Search']
	if len(movie_list) == 0:
		return None

	return movie_list[0]


def get_movie_details(imdb_id: str) -> Optional[Dict[str, Any]]:
	params: Dict[str, Any] = {
		'apikey': api_key,
		'i': imdb_id
	}

	full_url: str = api_url

	res: requests.Response = requests.get(
		full_url,
		params=params
	)

	parsed_json: Dict[str, Any] = res.json()
	response: bool = apply_safe_parse(extract_response, parsed_json, False)
	if not response:
		return None

	return parsed_json


def walk_folder(base_folder: str) -> List[Tuple[str, str, List[str]]]:
	rv: List[Tuple[str, str, List[str]]] = []
	complete_path: str = os.path.abspath(base_folder)

	root, dirs, files = next(os.walk(complete_path))

	for this_dir in dirs:
		this_dir_complete = os.path.join(root, this_dir)
		_, _, dir_files = next(os.walk(this_dir_complete))

		rv.append(
			(root, this_dir, dir_files)
		)

	return rv


def rename_folder(original: str, new: str) -> None:
	os.rename(original, new)


class OptionMovieNaming(Enum):
	IMDB = 'i'
	DIRNAME = 'd'
	MANUAL = 'm'


def prompt_movie_title(
		title_from_imdb: str, title_from_dirname: str, default_option: Optional[OptionMovieNaming]) -> str:
	"""
	Get a movie name, from one of the three options. If "manual" is selected, prompt the user from the title.

	:param title_from_imdb: the movie title as extracted from IMDB
	:param title_from_dirname: the movie title as extracted from the dirname
	:param default_option: which of the options (if any) should be a default option.
	:return: the selected movie title
	"""
	options_dict: Dict[OptionMovieNaming, str] = {
		OptionMovieNaming.IMDB: title_from_imdb,
		OptionMovieNaming.DIRNAME: title_from_dirname,
		OptionMovieNaming.MANUAL: 'Type in a movie name, manually'
	}

	if default_option is not None and default_option not in options_dict:
		log(f"Default option {default_option} is not a valid option itself", error=True, silent=True)
		default_option = None

	prompt_text: str = ""

	option: OptionMovieNaming
	option_description: str
	for option, option_description in options_dict.items():
		option_text: str = option.value
		if option == default_option:
			option_text = f"[{option_text.upper()}] (Default)"
		else:
			option_text = f"[{option_text.lower()}]"

		prompt_text += f"{option_text}: {option_description}\n"

	prompt_text += "Chose an option"
	if default_option is not None:
		prompt_text += ", or leave blank for default"
	prompt_text += ":"

	def option_movie_name_validator(raw: str) -> Tuple[bool, Optional[OptionMovieNaming]]:
		raw = raw.strip().lower()
		if len(raw) == 0:
			if default_option is not None:
				return True, default_option
			else:
				print("ERROR! There is no default option, you must choose one of the above.")

		chosen_option: Optional[OptionMovieNaming]
		try:
			chosen_option: OptionMovieNaming = OptionMovieNaming(raw)

			if chosen_option not in options_dict:
				chosen_option = None
		except ValueError:
			chosen_option = None

		if chosen_option is None:
			print(f"ERROR! {raw} isn't a valid option.")
			return False, None

		return True, chosen_option

	chosen_option: OptionMovieNaming = prompt(
		prompt_text, "Choose one of the valid options.", option_movie_name_validator)

	if chosen_option == OptionMovieNaming.IMDB:
		return title_from_imdb
	elif chosen_option == OptionMovieNaming.DIRNAME:
		return title_from_dirname
	else:  # If manual is chosen, prompt now for a  title
		manual_movie_title: str = prompt(
			"Input the movie title manually: ", "ERROR! Title can't be empty.",
			lambda title: (True, title.strip()) if len(title.strip()) > 0 else (False, None)
		)

		return manual_movie_title


def get_movie_for_dir(root: str, dirname: str, filenames: List[str]) -> Optional[Movie]:
	if len(filenames) == 0:
		log("Skipping empty dir {}".format(os.path.join(root, dirname)), error=True)
		return None

	# If a data file already exists on the folder, the information can be extracted from it
	if MovieConfig.MOVIE_CONFIG_FILENAME in filenames:
		movie_config_path: str = os.path.join(root, dirname, MovieConfig.MOVIE_CONFIG_FILENAME)
		with open(movie_config_path) as open_file:
			movie_config: MovieConfig = MovieConfig.from_json(json.load(open_file))
			imdb_id: str = movie_config.imdb_id
			movie: Optional[Movie] = Movie.from_imdb_id(imdb_id)
			optional_title: Optional[str] = movie_config.title
			if optional_title is not None:
				movie.set_title(optional_title)
	# If no file is present, it's the first time processing this directory
	else:
		# Attempt to construct the movie object from the dirname
		movie: Optional[Movie] = Movie.from_filename(dirname)
		if movie is None:
			movie = prompt_imdb_id()

			"""
			There are 3 different options to name the movie at this stage:
			
			- [I] (Default): use IMDB's name
			- [d]: use the name extracted from the directory itself
			- [m]: type in a name for the movie manually
			
			First, one of the valid options should be selected. In case of the options where nothing needs to be typed
			(options [I] and [d]), the corresponding value should be displayed.Entering nothing should select the 
			default option. If a title needs to be typed manually, it should be typed after the option has been chosen.
			"""

			movie_title, movie_year = extract_info_from_dirname(dirname, silent=True)

			chosen_movie_title: str = prompt_movie_title(movie.title, movie_title, OptionMovieNaming.IMDB)
			movie.set_title(chosen_movie_title)

			movie_config: MovieConfig = MovieConfig(movie.imdb_id, chosen_movie_title)
			movie_config.write_to_file(
				os.path.join(root, dirname, MovieConfig.MOVIE_CONFIG_FILENAME)
			)

	return movie


def rename_movie_folders(path: str, dry_run: bool = True) -> None:
	if not dry_run:
		log("Renaming folders at {}".format(path))
	else:
		log("Dry run renaming at {}".format(path))

	folder_and_filename_list: List[Tuple[str, str, List[str]]] = walk_folder(path)

	print("{} folder(s) detected".format(len(folder_and_filename_list)))
	for root, dirname, filenames in folder_and_filename_list:
		try:
			movie: Movie = get_movie_for_dir(root, dirname, filenames)

			if movie is None:
				print("No movie found for {}".format(os.path.join(root, dirname)), file=sys.stderr)
				continue

			new_dirname: str = movie.to_formatted_filename()

			print("{} => {}".format(
				os.path.join(root, dirname),
				os.path.join(root, new_dirname)
			))

			if not dry_run:
				rename_folder(os.path.join(root, dirname), os.path.join(root, new_dirname))
		except Exception as e:
			log("ERROR processing {} {} {}: {!r}".format(root, dirname, filenames, e))


class CountryEntry:
	_FIELD_LONG_NAME: str = 'long_name'
	_FIELD_SHORT_2LETTERS: str = 'short_2letters'
	_FIELD_SHORT_3LETTERS: str = 'short_3letters'

	_FILTERED_LONG_NAME_WORDS: str = ['and', 'of', 'the']

	long_name: Optional[str]
	_normalized_long_name: Optional[str]

	short_2letters: Optional[str]
	_normalized_short_2letters: Optional[str]

	short_3letters: Optional[str]
	_normalized_short_3letters: Optional[str]

	@staticmethod
	def _normalize_string(raw: str) -> str:
		return raw.strip().lower()

	@classmethod
	def _filter_long_name_words(cls, long_name_words: List[str]) -> List[str]:
		return list(filter(
			lambda x: x not in cls._FILTERED_LONG_NAME_WORDS,
			long_name_words
		))

	@classmethod
	def normalize_country(cls, raw: str) -> str:
		country_parts: List[str] = list(map(
			cls._normalize_string,
			raw.split()
		))

		if len(country_parts) > 1:
			country_parts = cls._filter_long_name_words(country_parts)

		return ''.join(country_parts)

	def __init__(self, long_name: Optional[str], short_2letters: Optional[str], short_3letters: Optional[str]):
		self.long_name = long_name
		self._normalized_long_name = type(self).normalize_country(long_name) \
			if long_name else None

		self.short_2letters = short_2letters
		self._normalized_short_2letters = type(self).normalize_country(short_2letters) \
			if short_2letters else None

		self.short_3letters = short_3letters
		self._normalized_short_3letters = type(self)._normalize_string(short_3letters) \
			if short_3letters else None

	@classmethod
	def make_single_abbrev(cls, long_name: Optional[str], abbrev: Optional[str]) -> 'CountryEntry':
		if abbrev is not None and len(abbrev) == 2:
			abbrev_2letters: str = abbrev
			abbrev_3letters: Optional[str] = None
		else:
			abbrev_2letters: Optional[str] = None
			abbrev_3letters: Optional[str] = abbrev

		return cls(long_name, abbrev_2letters, abbrev_3letters)

	def __repr__(self) -> str:
		return "<{}:[{}] {}:[{}] {}:[{}]>".format(
			self.long_name, self.get_normalized_long_name(),
			self.short_2letters, self.get_normalized_short_2letters(),
			self.short_3letters, self.get_normalized_short_3letters()
		)

	def get_normalized_long_name(self) -> Optional[str]:
		return self._normalized_long_name

	def get_normalized_short_2letters(self) -> Optional[str]:
		return self._normalized_short_2letters

	def get_normalized_short_3letters(self) -> Optional[str]:
		return self._normalized_short_3letters

	@staticmethod
	def _get_preference(first_choice: Optional[str], second_choice: Optional[str]) -> Optional[str]:
		return first_choice if first_choice is not None else second_choice

	def get_shorter_abbrev(self) -> Optional[str]:
		return type(self)._get_preference(self.short_2letters, self.short_3letters)

	def get_longer_abbrev(self) -> Optional[str]:
		return type(self)._get_preference(self.short_3letters, self.short_2letters)

	def to_dict(self) -> Dict[str, Optional[str]]:
		return {
			type(self)._FIELD_LONG_NAME: self.long_name,
			type(self)._FIELD_SHORT_2LETTERS: self.short_2letters,
			type(self)._FIELD_SHORT_3LETTERS: self.short_3letters
		}

	@classmethod
	def from_dict(cls, d: Dict[str, Optional[str]]) -> 'CountryEntry':
		return cls(
			d.get(cls._FIELD_LONG_NAME),
			d.get(cls._FIELD_SHORT_2LETTERS),
			d.get(cls._FIELD_SHORT_3LETTERS)
		)


class CountryDb:
	_SINGLETON_INSTANCE: Optional['CountryDb'] = None
	_SINGLETON_SOURCE_FILENAME: str = 'country_db.json'

	_long_name_mapping: Dict[str, CountryEntry]
	_short_2letters_mapping: Dict[str, CountryEntry]
	_short_3letters_mapping: Dict[str, CountryEntry]

	@staticmethod
	def _add_entry_to_mapping(
			new_entry: CountryEntry, mapping: Dict[str, CountryEntry],
			key: Callable[[CountryEntry], str], field_name: str
	):
		new_entry_key: str = key(new_entry)
		if new_entry_key is None:
			return
		existing_entry: Optional[CountryEntry] = mapping.get(new_entry_key)
		if existing_entry is not None:
			raise ValueError("{} and {} clash, shared {}".format(new_entry, existing_entry, field_name))

		mapping[new_entry_key] = new_entry

	def __init__(self, entries: List[CountryEntry]):
		self._long_name_mapping = {}
		self._short_2letters_mapping = {}
		self._short_3letters_mapping = {}

		for entry in entries:
			self.insert_entry(entry)

	def insert_entry(self, entry: CountryEntry) -> None:
		type(self)._add_entry_to_mapping(
			entry, self._long_name_mapping, CountryEntry.get_normalized_long_name, "long name")
		type(self)._add_entry_to_mapping(
			entry, self._short_2letters_mapping, CountryEntry.get_normalized_short_3letters, "short 2 letters name"
		)
		type(self)._add_entry_to_mapping(
			entry, self._short_3letters_mapping, CountryEntry.get_normalized_short_3letters, "short 3 letters name"
		)

	@staticmethod
	def _maybe_normalize(raw: str, normalize: bool) -> str:
		if normalize:
			return CountryEntry.normalize_country(raw)
		return raw

	def find_by_long_name(self, long_name: str, normalize: bool = True) -> Optional[CountryEntry]:
		return self._long_name_mapping.get(type(self)._maybe_normalize(long_name, normalize))

	def find_by_short_2letters(self, short_2letters: str, normalize: bool = True) -> Optional[CountryEntry]:
		return self._short_2letters_mapping.get(type(self)._maybe_normalize(short_2letters, normalize))

	def find_by_short_3letters(self, short_3letters: str, normalize: bool = True) -> Optional[CountryEntry]:
		return self._short_3letters_mapping.get(type(self)._maybe_normalize(short_3letters, normalize))

	def find_anywhere(self, country: str) -> Optional[CountryEntry]:
		normalized_name: str = CountryEntry.normalize_country(country)
		finders: List[Callable[[str, bool], Optional[CountryEntry]]] = [
			self.find_by_long_name,
			self.find_by_short_2letters,
			self.find_by_short_3letters
		]

		for finder in finders:
			found: Optional[CountryEntry] = finder(normalized_name, False)
			if found is not None:
				return found

		return None

	def _get_sorted_entries(self) -> List[CountryEntry]:
		entries: List[CountryEntry] = list(self._short_3letters_mapping.values())
		return sorted(entries, key=CountryEntry.get_normalized_short_3letters)

	def __repr__(self) -> str:
		return "<{}>:\n".format(type(self)) + "\n".join(map(str, self._get_sorted_entries()))

	def to_serializable_list(self) -> List[Dict[str, Optional[str]]]:
		return list(map(
			CountryEntry.to_dict,
			self._long_name_mapping.values()
		))

	@classmethod
	def from_serializable_list(cls, l: List[Dict[str, Optional[str]]]) -> 'CountryDb':
		entries: List[CountryEntry] = list(map(CountryEntry.from_dict, l))
		return cls(entries)

	@classmethod
	def get_singleton(cls) -> 'CountryDb':
		if cls._SINGLETON_INSTANCE is None:
			with open(cls._SINGLETON_SOURCE_FILENAME, encoding='utf-8') as source_file:
				raw_db: List[Dict[str, Optional[str]]] = json.load(source_file)
				cls._SINGLETON_INSTANCE = cls.from_serializable_list(raw_db)
		return cls._SINGLETON_INSTANCE


BASE_PATH_ENV_VAR: str = 'IMDB_RENAMER_BASE_PATH'
BASE_PATH_DEFAULT: str = '.'

DRY_RUN_ENV_VAR: str = 'IMDB_DRY_RUN'
DRY_RUN_DEFAULT: bool = False


def main() -> None:
	try:
		log('START')

		base_path: str = os.path.abspath(os.environ.get(BASE_PATH_ENV_VAR, BASE_PATH_DEFAULT))
		dry_run: bool = bool(os.environ.get(DRY_RUN_ENV_VAR, DRY_RUN_DEFAULT))

		log('Directory: {}'.format(base_path))

		rename_movie_folders(base_path, dry_run)

		log("END")
	except Exception as e:
		log("EXECUTION ERROR: {!r}".format(e), error=True)
		log("Error details: {}".format(traceback.format_exc()))


if __name__ == '__main__':
	main()
