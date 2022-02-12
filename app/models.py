from datetime import datetime, timedelta
from app import db, login, elasticsearch, app
from flask_login import UserMixin
from flask import url_for
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.request, json
from urllib.error import HTTPError
from elasticsearch_dsl import Document, Text, Search, Q
from elasticsearch_dsl.query import MultiMatch, Match
from elasticsearch import ElasticsearchException
from sqlalchemy import extract
from enum import Enum, auto


class SearchableMixin(object): # COULD MOVE FUNCTIONS DEPENDENT ON ELASTICSEARCH OUTSIDE

	def get_document(self):
		raise NotImplementedError("Need to define document structure for model")

	# def save(self):
	# 	# try:
	# 	# 	self.get_document().save(index=cls.__tablename__, using=elasticsearch)
	# 	# except ElasticsearchException as e:
	# 	# 	db.session.rollback()
	# 	# 	raise(e)
	# 	raise NotImplementedError("Need to define save method for model")


	@classmethod
	def search(cls, query, page, per_page):
		# query is a query object (Q)
		s = Search(index=cls.__tablename__, using=elasticsearch)
		s = s[(page - 1) * per_page: page * per_page]
		s = s.query(query)
		response = s.execute()
		ids = [int(hit.meta.id) for hit in response.hits]
		total = response.hits.total.value
		if total == 0:
			return cls.query.filter_by(id=0), 0
		when = [(_id, i) for (i, _id) in enumerate(ids)]
		return cls.query.filter(cls.id.in_(ids)).order_by(
			db.case(when, value=cls.id)), total

	@classmethod
	def before_commit(cls, session):
		session._changes = {
			'add': list(session.new),
			'update': list(session.dirty),
			'delete': list(session.deleted)
		}

	@classmethod
	def after_commit(cls, session):
		for obj in session._changes['add']:
			if isinstance(obj, SearchableMixin):
				obj.get_document().save(index=obj.__tablename__, using=elasticsearch)
		for obj in session._changes['update']:
			if isinstance(obj, SearchableMixin):
				obj.get_document().save(index=obj.__tablename__, using=elasticsearch)
		for obj in session._changes['delete']:
			if isinstance(obj, SearchableMixin):
				s = Search(using=elasticsearch, index=obj.__tablename__).query("match", _id=obj.id)
				s.delete()
		session._changes = None

	@classmethod
	def delete_documents(cls):
		elasticsearch.delete_by_query(index=cls.__tablename__, body={"query": {"match_all": {}}})

	@staticmethod
	def init_index():
		raise NotImplementedError('Need to create index initialization for model')

	@staticmethod
	def delete_index():
		raise NotImplementedError('Need to create index deletion for model')

	@classmethod
	def reindex(cls):
		# getattr(locals().get(f'{cls.__name__}Index'), 'init')(index=cls.__tablename__) # Create index and populate mappings
		for obj in cls.query:
			obj.get_document().save(index=cls.__tablename__, using=elasticsearch)

db.event.listen(db.session, 'before_commit', SearchableMixin.before_commit)
db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)


book_authors = db.Table('book_authors',
	db.Column('book_id', db.Integer, db.ForeignKey('book.id'), primary_key=True),
	db.Column('author_id', db.Integer, db.ForeignKey('author.id'), primary_key=True)
	)


class User(UserMixin, db.Model):
	id = db.Column(db.Integer, primary_key=True)
	username = db.Column(db.String(64), index=True, unique=True)
	password_hash = db.Column(db.String(128))

	def __repr__(self):
		return f'<User {self.username}>'

	def set_password(self, password):
		self.password_hash = generate_password_hash(password)

	def check_password(self, password):
		return check_password_hash(self.password_hash, password)

@login.user_loader
def load_user(id):
	return User.query.get(int(id))

class Category(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	name = db.Column(db.String(32), nullable=False, index=True)
	books = db.relationship('Book', backref='book_category', lazy='dynamic')

	def __repr__(self):
		return f'<Category {self.name}>'

	@staticmethod
	def get_category_names():
		return [obj.name for obj in Category.query.all()]


class Author(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	name = db.Column(db.String(64), nullable=False, index=True)

	def __repr__(self):
		return f'<Author {self.name}>'

	def get_works(self):
		return Book.query.join(book_authors, 
			(book_authors.c.book_id == Book.id)).filter(
			book_authors.c.author_id == self.id).order_by(Book.full_title)

class BookStatus(Enum):
	ON_LOAN = auto()
	ON_SHELF = auto()
	MISSING = auto()

class Book(SearchableMixin, db.Model):
	id = db.Column(db.Integer, primary_key=True)
	isbn_10 = db.Column(db.String(10), index=True, nullable=True)
	isbn_13 = db.Column(db.String(13), index=True, nullable=True) # Older books dont have ISBN-13
	full_title = db.Column(db.String(100), nullable=False)
	authors = db.relationship('Author', secondary=book_authors, primaryjoin=(book_authors.c.book_id == id), lazy='subquery', backref='works')
	pages = db.Column(db.Integer)
	publish_date = db.Column(db.Date)
	category = db.Column(db.Integer, db.ForeignKey('category.id')) # We assume that each book has at most 1 category
	cover = db.Column(db.Text)
	copies = db.relationship('Copy', backref='work', lazy='dynamic', cascade="all, delete")
	# loans = db.relationship('Loan', backref='loaned_book', lazy='dynamic')

	def __repr__(self):
		return f'<Book {self.id}: {self.full_title}>'

	@staticmethod
	def get_by_id(id):
		return Book.query.get_or_404(int(id))

	@staticmethod
	def get_by_isbn(isbn):
		return Book.query.filter_by(isbn_13=isbn).first()

	def _author_repr(self):
		# Return a str representation of authors
		return ', '.join([a.name for a in self.authors])

	def get_document(self):
		return BookIndex(
			_id=self.id,
			full_title=self.full_title,
			authors = self._author_repr()
		)

	# def save(self):
	# 	try:
	# 		self.get_document().save(index='book', using=elasticsearch)
	# 	except ElasticsearchException as e:
	# 		db.session.rollback()
	# 		raise e

	@staticmethod
	def init_index():
		BookIndex.init(index='book', using=elasticsearch)

	@staticmethod
	def delete_index():
		elasticsearch.indices.delete(index='book', ignore=[400, 404])


	# TOP LEVEL FUNCTIONS FOR COPIES OF BOOKS

	def get_copies(self):
		return self.copies.count(), self.copies.all()

	def num_total_copies(self):
		return self.copies.count()

	def get_statuses(self):
		status = [0, 0, 0] # Num on shelf, num on loan. num missing
		for copy in self.copies.all():
			if copy.is_on_shelf():
				status[0] += 1
			elif copy.is_on_loan():
				status[1] += 1
			else:
				status[2] += 1
		return status

	def generate_copies(self, num_copies=1):
		if num_copies < 1:
			return []
		for _ in range(num_copies):
			new_copy = Copy()
			self.copies.append(new_copy)
		return self.copies.all()

	def num_available_copies(self):
		return sum(1 for copy in self.copies if copy.is_on_shelf())

	def get_available_copy(self):
		return self.copies.filter(~Copy.loans.any(returned=False)).first()

	def has_available_copy(self):
		copy = self.get_available_copy()
		return copy is not None

	def all_copies_available(self):
		return all([copy.is_on_shelf() for copy in self.copies.all()])

	def delete_copies(self, num_delete): # returns ids for cascade delete
		# Only delete copies if there are enough on shelf
		if self.num_total_copies() <= num_delete or self.num_available_copies() < num_delete:
			return []
		return Copy._delete_copies(self.id, num_delete)

	def take_out_loan(self, loan, loanee):
		copy = self.get_available_copy()
		if not copy: # No available copies
			return False
		result = copy._attach_to_loan(loan)
		if not result:
			return False
		result = loanee._attach_loan(loan)
		if not result:
			_ = copy._detach_loan(loan)
		return result

	def get_all_loans(self):
		return Loan._get_book_loans(self.id)

	# TOP LEVEL FUNCTIONS FOR SEARCHING BOOKS

	@staticmethod
	def search_keyword(query_str, page, per_page):
		q = Q("multi_match", query=query_str, fields=['*'], fuzziness=1)
		return Book.search(q, page, per_page)

	@staticmethod
	def search_title(query_str, page, per_page):
		q = Q("match", full_title={'query':query_str, 'fuzziness': 1})
		return Book.search(q, page, per_page)

	@staticmethod
	def search_author(query_str, page, per_page, exact=False):
		if exact:
			books = Book.query.join(Book.authors).filter_by(name=query_str)
			total = len(books.all())
			return books.paginate(page, app.config['BOOKS_PER_PAGE'], False), total
		q = Q("match", authors={'query': query_str, 'fuzziness': 1})
		return Book.search(q, page, per_page)

	@staticmethod
	def search_advanced(query_dict, page, per_page):
		# Returns a paginated object for books
		if len(query_dict) < 1:
			return None, 0

		# Fuzzy search for book title and authors
		q1, q2 = None, None
		if 'full_title' in query_dict:
			q1 = Q("match", full_title={'query':query_dict['full_title'], 'fuzziness': 1})
			# query_dict.pop('full_title')
		if 'authors' in query_dict:
			q2 = Q("match", authors={'query': query_dict['authors'], 'fuzziness': 1})
			# query_dict.pop('authors')
		q = q1 & q2 if (q1 is not None and q2 is not None) else (q1 or q2)

		if q:
			books, _ = Book.search(q, page, per_page)
		else:
			books = Book.query

		# Exact search for other book properties
		if 'category' in query_dict:
			books = books.join(Book.book_category).filter_by(name=query_dict['category'])
			# query_dict.pop('category')
		if 'publish_date' in query_dict:
			books = books.filter(extract('year', Book.publish_date) == query_dict['publish_date']) # query date must be int
			# query_dict.pop('publish_date')

		# Get the rest
		# if len(query_dict) > 0:
		# 	books = books.filter_by(**query_dict)
		total = len(books.all())
		books = books.paginate(page, app.config['BOOKS_PER_PAGE'], False)
		return books, total


class BookIndex(Document):
	full_title = Text()
	authors = Text()

class Copy(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	book_id = db.Column(db.Integer, db.ForeignKey('book.id'))
	loans = db.relationship('Loan', backref='loaned_copy', lazy='dynamic', cascade="all, delete")

	def __repr__(self):
		return f'<Copy of {self.work.full_title}: ID #{self.id}>'

	def get_current_loan(self):
		return self.loans.filter_by(returned=False).first()


	def _get_status(self):
		curr_loan = self.get_current_loan()
		if not curr_loan:
			return BookStatus.ON_SHELF
		if curr_loan.is_overdue():
			return BookStatus.MISSING
		return BookStatus.ON_LOAN

	def is_on_loan(self):
		return self._get_status() == BookStatus.ON_LOAN

	def is_on_shelf(self):
		return self._get_status() == BookStatus.ON_SHELF

	def is_missing(self):
		return self._get_status() == BookStatus.MISSING

	def _attach_to_loan(self, loan):
		# Attach loan object to this copy
		self.loans.append(loan)
		return True

	def _detach_loan(self, loan):
		self.loans.remove(loan)
		return True


	@staticmethod
	def _delete_copies(book_id, num_delete): 
		sq = Copy.query.filter(Copy.book_id==book_id, ~Copy.loans.any(returned=False)).limit(num_delete)
		return [id[0] for id in sq.with_entities(Copy.id).all()]


class Loan(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	copy_id = db.Column(db.Integer, db.ForeignKey('copy.id'))
	loanee = db.Column(db.Integer, db.ForeignKey('loanee.id'))
	out_timestamp = db.Column(db.Date, index=True, default=datetime.today().date) # Date book was taken out
	in_timestamp = db.Column(db.Date, index=True, nullable=False) # Date book must be returned or was returned
	returned = db.Column(db.Boolean, index=True, nullable=False, default=False) # Loan is still open or closed

	def __repr__(self):
		return f'<Loan [RETURNED={self.returned}] book={self.loaned_copy.work.full_title} to {self.loaning_person.name} at {self.out_timestamp}>'

	def extend_loan_period(self, delta=1, unit='weeks'):
		# timedelta is length of time to extend loan period
		td = timedelta(**{unit: delta})
		if td.days > 0 and not self.returned:
			self.in_timestamp += td
			return True
		return False

	def close(self):
		if not self.returned:
			self.in_timestamp = datetime.now().date()
			self.returned = True
			return True
		return False

	def is_active(self):
		# Return True iff book is still out on loan 
		return not self.returned

	def is_overdue(self):
		return self.is_active() and (self.in_timestamp < datetime.now().date())

	# @staticmethod
	# def get_loans(phone_num):
	# 	# Returns a query object
	# 	return Loan.query.filter_by(phone_num=phone_num).filter(Loan.book_id.isnot(None))

	# @staticmethod
	# def _get_book_active_loans(book_id):
	# 	return Loan.filter_by(returned=False).join(Loan.copy_id).filter_by(book_id=book_id)

	@staticmethod
	def _get_book_loans(book_id):
		all_loans = Loan.query.join(Copy).filter_by(book_id=book_id)
		current = all_loans.filter(Loan.returned == False)
		past = all_loans.filter(Loan.returned == True)
		return {
			'current': (current.count(), current.all()),
			'past': (past.count(), past.all())
		}

	@staticmethod
	def get_expiring(delta=1, unit='weeks'):
		# returns a query object
		td = timedelta(**{unit: delta})
		if td.days > 0:
			date_limit = datetime.today().date() + td
			return Loan.query.filter(Loan.returned == False, Loan.in_timestamp <= date_limit).order_by(Loan.in_timestamp)
		return Loan.query.filter_by(id=0)

class Loanee(SearchableMixin, db.Model):
	id = db.Column(db.Integer, primary_key=True)
	phone_num = db.Column(db.String(14), index=True) # some people don't provide phone numbers
	name = db.Column(db.String(32), nullable=False)
	loans = db.relationship('Loan', backref='loaning_person', lazy='dynamic')

	def get_document(self):
		return LoaneeIndex(
			_id=self.id,
			name=self.name,
		)

	def _attach_loan(self, loan):
		self.loans.append(loan)
		return True

	def _detach_loan(self, loan):
		self.loans.remove(loan)
		return True

	@staticmethod
	def init_index():
		LoaneeIndex.init(index='loanee', using=elasticsearch)

	@staticmethod
	def delete_index():
		elasticsearch.indices.delete(index='book', ignore=[400, 404])
	
	@staticmethod
	def search_by_name(query_str):
		q = Q("match", name={'query':query_str, 'fuzziness': 1})
		return Loanee.search(q, 1, 5)

	@staticmethod
	def get_by_phone(phone_num):
		# This is an exact search; it should find at most one loanee
		return Loanee.query.filter_by(phone_num=phone_num).first()

	@staticmethod
	def get_by_name(name):
		# This is an exact search; it should find at most one loanee
		return Loanee.query.filter_by(name=name).first()

	@staticmethod
	def get_loans_by_phone(phone_num):
		# Returns all loans made by person with phone number 'phone_num'
		loanee = Loanee.get_by_phone(phone_num)
		if not loanee:
			return [], 0
		loans = loanee.loans
		return loans.all(), loans.count()

	@staticmethod
	def get_loans_by_name(name):
		# Returns all loans made by person with phone number 'phone_num'
		loanee = Loanee.get_by_name(name)
		if not loanee:
			return [], 0
		loans = loanee.loans
		return loans.all(), loans.count()


	@staticmethod
	def get_by_id(id):
		# This is an exact search; it should find at most one loanee 
		return Loanee.query.get_or_404(int(id))

	@staticmethod
	def get_loans_by_id(id):
		# Returns all loans made by person with id 'id'
		loanee = Loanee.get_by_id(id)
		if not loanee:
			return [], 0
		loans = loanee.loans
		return loans.all(), loans.count()

	# def save(self):
	# 	try:
	# 		self.get_document().save(index='loanee', using=elasticsearch)
	# 	except ElasticsearchException as e:
	# 		db.session.rollback()
	# 		raise e

class LoaneeIndex(Document):
	name = Text()








