import json
import os
import re
import sys
from string import Template
from typing import Dict, Any, Optional, List, TypeVar, Callable, Tuple, Pattern, Match, Set, TextIO

import requests

api_url: str = 'http://www.omdbapi.com/'
api_key: str = 'fb47edfb'

folder_name_template: Template = Template('${title} (${country} ${year}, ${director} - ${actors})')

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
	country: Optional[str]

	def __init__(
			self,
			title: str, year: int, imdb_id: str,
			actors: Optional[List[str]] = None, director: Optional[str] = None, country: Optional[str] = None
	):
		self.set_title(title)
		self.year = year
		self.imdb_id = imdb_id
		self.set_actors(actors)
		self.director = cleanse_string(director, wildcard=' - ')
		self.country = cleanse_string(country, wildcard=' - ')

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

	def _calculate_actors(self) -> Optional[str]:
		if self._raw_actors is None:
			return None

		return ', '.join(self._raw_actors)

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

	def _add_details(self, actors: List[str], director: str, country: str) -> None:
		self.set_actors(actors)
		self.director = cleanse_string(director, wildcard=' - ')
		self.country = cleanse_string(country, wildcard=' - ')

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


def get_movie_for_dir(root: str, dirname: str, filenames: List[str]) -> Optional[Movie]:
	if len(filenames) == 0:
		log("Skipping empty dir {}".format(os.path.join(root, dirname)), error=True)
		return None

	if MovieConfig.MOVIE_CONFIG_FILENAME in filenames:
		movie_config_path: str = os.path.join(root, dirname, MovieConfig.MOVIE_CONFIG_FILENAME)
		with open(movie_config_path) as open_file:
			movie_config: MovieConfig = MovieConfig.from_json(json.load(open_file))
			imdb_id: str = movie_config.imdb_id
			movie: Optional[Movie] = Movie.from_imdb_id(imdb_id)
			optional_title: Optional[str] = movie_config.title
			if optional_title is not None:
				movie.set_title(optional_title)
	else:
		movie: Optional[Movie] = Movie.from_filename(dirname)
		if movie is None:
			movie = prompt_imdb_id()

			movie_title, movie_year = extract_info_from_dirname(dirname, silent=True)

			optional_title: Optional[str] = prompt_title(movie_title)
			if optional_title is not None:
				movie.set_title(optional_title)

			movie_config: MovieConfig = MovieConfig(movie.imdb_id, optional_title)
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


if __name__ == '__main__':
	def main() -> None:
		try:
			log('START')

			base_path: str = os.path.abspath('./test')
			log('Directory: {}'.format(base_path))

			rename_movie_folders(base_path, dry_run=False)

			log("END")
		except Exception as e:
			log("EXECUTION ERROR: {!r}".format(e), error=True)


	main()
