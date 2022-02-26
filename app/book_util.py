from typing import List, Tuple
from app.models import Book, Category, Author, Loan, Copy, Loanee
from flask import abort, url_for
from dateparser import parse as parse_date
import isbn
import urllib.request, json
from urllib.error import HTTPError

DATE_PARSER_SETTINGS = {
			"DATE_ORDER": "YMD",
			"PREFER_DAY_OF_MONTH": 'first',
            }

class BookInfoBuilder:
    """
    Performs the lookup for book information based on ISBN-13.
    """

    def __init__(self, isbn_str: str) -> None:
        self.reset(isbn_str)

    def _parse_isbn(self, data):
        book_isbn = data.get('isbn_10') or data.get('isbn_13')
        if book_isbn:
            book_isbn = isbn.ISBN(book_isbn[0]) # book_isbn is a list
            return book_isbn.isbn10(), book_isbn.isbn13()
        return None, None
        
    def _parse_title(self, data):
        return data.get('full_title', data.get('title'))
        
    def _parse_pages(self, data):
        return data.get('number_of_pages')
        
    def _parse_publish_date(self, data):
        if "publish_date" in data:
            if data["publish_date"]:
                date = parse_date(data["publish_date"], settings=DATE_PARSER_SETTINGS)
                return str(date)[:10]
        return None

    def _parse_authors(self, data):
        return data.get('authors', [])

    def reset(self, isbn_str: str) -> None:
        val_isbn = isbn.ISBN(isbn_str)
        self._isbn_10, self._isbn_13 = val_isbn.isbn10(), val_isbn.isbn13()
        self._data = dict()

    def get_info(self):
        info_url = f'https://openlibrary.org/isbn/{self._isbn_13}.json'
        try:
            with urllib.request.urlopen(info_url) as response:
                payload = response.read()
                self._raw = json.loads(payload)
            full_title = self._parse_title(self._raw)
            pages = self._parse_pages(self._raw)
            publish_date = self._parse_publish_date(self._raw)
            status_code = 200
        except HTTPError:
            full_title, pages, publish_date = '','',''
            status_code = 400
        finally:
            self._data['isbn_10'] = self._isbn_10
            self._data['isbn_13'] = self._isbn_13
            self._data['full_title'] = full_title
            self._data['pages'] = pages
            self._data['publish_date'] = publish_date
            self._data['status_code'] = status_code
        return self

    def get_cover(self):
        cover_url = f'https://covers.openlibrary.org/b/isbn/{self._isbn_13}-M.jpg?default=False'
        try:
            _ = urllib.request.urlopen(cover_url)
        except HTTPError:
            cover_url = ''
        finally:
            self._data['cover'] = cover_url
        return self

    def get_authors(self):
        if self._data['status_code'] == 400:
            self._data['authors'] = ''
        elif self._data['status_code'] == 200:
            authors = self._parse_authors(self._raw)
            author_names = []
            for author in authors:
                url = 'https://openlibrary.org{}.json'.format(author['key'])
                with urllib.request.urlopen(url) as response:
                    payload = response.read()
                    name = json.loads(payload)['name']
                author_names.append(name)
            self._data['authors'] = ', '.join(author_names)
        return self

    def data(self) -> dict:
        return self._data
    

class BookBuilder:
    """
    Builds a Book object (as well as Category and Author as necessary) 
    """

    def __init__(self, premade=None) -> None:
        # 'premade' is a Book object that we want to edit
        self.reset(premade)

    def reset(self, premade=None) -> None:
        if premade is None:
            self._book = Book()
        else:
            self._book = premade

    def book(self) -> Book:
        self._generate_document() # Create document for elasticsearch indexing
        return self._book

    def _delete_copies_from_collection(self, new_total: int):
        num_existing = self._book.num_total_copies()
        # print(f'Book currently has {num_existing} copies in collection.')
        if num_existing > new_total:
            # Check if there are enough copies to delete
            num_delete = num_existing - new_total
            if self._book.num_available_copies() >= num_delete:
                to_delete = self._book.delete_copies(num_delete)
                if len(to_delete) == 0:
                    abort(500, f'500: Unable to delete {num_delete} copies of Book <{self._book.full_title}>')
                else:
                    return to_delete
            else:
                abort(400, f"400: Can't delete more copies of Book <{self._book.full_title}> than are available.")
        abort(500, f"Wrong method used to delete copies of Book <{self._book.full_title}>")


    def _add_copies_to_collection(self, new_total: int, max_copies: int):
        num_existing = self._book.num_total_copies()
        if num_existing < new_total and new_total <= max_copies:
            num_create = new_total - num_existing
            _ = self._book.generate_copies(num_create)
            return num_create
        abort(400, f"400: Total number of copies exceeds max number of copies. No new copies of Book <{self._book.full_title}> were created")

    def enter_title(self, title: str):
        if title is None or title == '':
            abort(400, f'Book must have a title in order to be added to the collection.')
        self._book.full_title = title
        return self

    def edit_title(self, title: str):
        if title is not None and self._book.full_title != title:
            self._book.full_title = title
        return self

    def enter_isbn13(self, isbn_13: str):
        if isbn_13 is not None and isbn_13 != '':
            q_book = Book.get_by_isbn(isbn_13)
            if q_book:
                abort(400, f'Book <ISBN-13 {q_book.isbn_13}> is already in collection')
            self._book.isbn_13 = isbn_13
        return self

    def enter_isbn10(self, isbn_10: str):
        if isbn_10 is not None and isbn_10 != '':
            self._book.isbn_10 = isbn_10
        return self

    def enter_category(self, category_name: str):
        if not category_name or category_name == '':
            category = Category.query.filter_by(name='MISSING').first()
        else:
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(name=category_name)
        self._book.book_category = category
        return self

    def edit_category(self, category_name: str):
        if category_name and category_name != '':
            if category_name != self._book.book_category.name:
                category = Category.query.filter_by(name=category_name).first()
                if not category:
                    category = Category(name=category_name)
                self._book.book_category = category
        return self

    def enter_pages(self, num_pages: int):
        if num_pages:
            self._book.pages = num_pages
        return self

    def edit_pages(self, num_pages: int):
        if num_pages and num_pages != self._book.pages:
            self._book.pages = num_pages
        return self

    def enter_publish_date(self, date_str: str):
        if date_str and date_str != '':
            self._book.publish_date = parse_date(date_str, settings= DATE_PARSER_SETTINGS)
        return self

    def edit_publish_date(self, date_str: str):
        return self.enter_publish_date(date_str)

    def enter_num_copies(self, num_copies: int, max_copies: int):
        if num_copies > 0 and num_copies < max_copies:
            generate_num = num_copies 
        else:
            generate_num = 1
        _ = self._book.generate_copies(generate_num)
        return self

    def edit_num_copies(self, num_copies: int, max_copies: int) -> Tuple[list, int]:
        num_existing = self._book.num_total_copies()
        num_created, deleted_ids = 0, []
        if num_existing < num_copies:
            num_created = self._add_copies_to_collection(num_copies, max_copies)
        elif num_copies < num_existing:
            deleted_ids = self._delete_copies_from_collection(num_copies)
            num_created = -len(deleted_ids)
        return deleted_ids, num_created

    def enter_cover(self, cover_url: str):
        if cover_url:
            if cover_url == '':
                self._book.cover = url_for('static', filename='nocover.jpg')
            else:
                self._book.cover = cover_url
        return self

    def enter_authors(self, author_str: str):
        if author_str is None or author_str == '':
            abort(400, f'Book must have at least one author in order to be added to the collection.')
        for author_token in author_str.split(', '):
            author_token = author_token.strip().title()
            author = Author.query.filter_by(name=author_token).first()
            if not author:
                author = Author(name=author_token)
            self._book.authors.append(author)
        return self

    def edit_authors(self, author_str: str):
        if author_str is None or author_str == '':
            abort(400, f'Book must have at least one author in order to be added to the collection.')
        existing_authors = [a.name for a in self._book.authors]
        new_author_tok = [a.strip().title() for a in author_str.split(', ')]
        new_authors = []
        
        if existing_authors != new_author_tok:
            for author_token in new_author_tok:
                author = Author.query.filter_by(name=author_token).first()
                if not author:
                    author = Author(name=author_token)
                new_authors.append(author)
            self._book.authors = new_authors
        return self

    def _generate_document(self):
        # For indexing with elasticsearch
        self._book.save_document()


            





