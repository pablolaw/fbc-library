from app.models import User, Book, Category, Author, Loan, Copy, Loanee
from elasticsearch_dsl import Document, Text, Search, Q
from sqlalchemy import extract

class SearchBuilder:

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._search = Search()

    def search(self) -> Search:
        return self._search

    def add_fuzzy_title(self, query):
        if query is not None and query != '':
            q = Q("match", full_title={'query':query, 'fuzziness': 1})
            self._search.add_fuzzy_query(q)
            self._search._record_query(f"Title: {query}")
        return self

    def add_fuzzy_author(self, query):
        if query is not None and query != '':
            q = Q("match", authors={'query': query, 'fuzziness': 1})
            self._search.add_fuzzy_query(q)
            self._search._record_query(f"Author(s): {query}")
        return self

    def add_fuzzy_keyword(self, query):
        if query is not None and query != '':
            q = Q("multi_match", query=query, fields=['*'], fuzziness=1)
            self._search.add_fuzzy_query(q)
            self._search._record_query(f"Keyword: {query}")
        return self

    def add_category(self, query):
        if query is not None and query != 'None' and query != '':
            self._search.add_exact_query('category', query)
            self._search._record_query(f"Category: {query}")
        return self

    def add_date(self, query):
        if query is not None and query != '':
            self._search.add_exact_query('publish_date', query)
            self._search._record_query(f"Publication Year: {query}")
        return self

    def search_repr(self) -> None:
        return str(self._search)



class Search:
    """
    For building search queries for books.
    """

    def __init__(self) -> None:
        self._fuzzy_query = None
        self._exact_query = dict()
        self._query_repr = []

    def add_fuzzy_query(self, query_obj) -> None:
        if self._fuzzy_query:
            self._fuzzy_query = self._fuzzy_query & query_obj
        else:
            self._fuzzy_query = query_obj

    def add_exact_query(self, key, query) -> None:
        self._exact_query[key] = query

    def _execute_category_search(self, books):
        if 'category' in self._exact_query:
            cat_query = self._exact_query['category']
            books = books.join(Book.book_category).filter_by(name=cat_query)
        return books

    def _execute_date_search(self, books):
        if 'publish_date' in self._exact_query:
            date_query = self._exact_query['publish_date']
            books = books.filter(extract('year', Book.publish_date) == date_query) # query date must be int
        return books

    def _record_query(self, query_str):
        self._query_repr.append(query_str)

    def __repr__(self) -> str:
        return ', '.join(self._query_repr)

    def execute(self, page, per_page):
        """
        Returns the number of books found and a list of the 
        books returned by the search.
        """
        if self._fuzzy_query:
            books, _ = Book.search(self._fuzzy_query, page, per_page)
        elif len(self._exact_query) > 0:
            books = Book.query
        else:
            return Book.query.filter_by(id=0).paginate(page, per_page, False), 0


        books = self._execute_category_search(books)
        books = self._execute_date_search(books)
        
        total = len(books.all())
        books = books.paginate(page, per_page, False)
        return books, total

        
