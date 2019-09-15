import json
import re
import sys
from typing import List, Dict, Optional, Callable, Pattern, Match


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
