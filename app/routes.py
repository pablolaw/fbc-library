from flask import render_template, flash, redirect, url_for, request, session, g, jsonify, abort
from app import db, app
from app.forms import LoginForm, BookLookUpForm, BookEditForm, BookEntryForm, EmptyForm, SearchForm, AdvancedSearchForm, LoanBookForm, LoanExtendForm, LoanPhoneForm
from flask_login import current_user, login_user, logout_user, login_required
from app.models import User, Book, Category, Author, Loan
from werkzeug.urls import url_parse
from werkzeug.http import HTTP_STATUS_CODES
import urllib.request, json
from urllib.error import HTTPError
from datetime import datetime, timedelta

# UTILITY FUNCTIONS (should probably put these somewhere else...)
from dateparser import parse as parse_date
import isbn

def parse_isbn(data):
	book_isbn = data.get('isbn_10') or data.get('isbn_13')
	if book_isbn:
		book_isbn = isbn.ISBN(book_isbn[0]) # book_isbn is a list
		return book_isbn.isbn10(), book_isbn.isbn13()
	return None, None

def parse_title(data):
	return data.get('full_title', data.get('title'))

def parse_pages(data):
	return data.get('number_of_pages')

def parse_publish_date(data):
	if "publish_date" in data:
		# TODO: make constant
		settings = {
			"DATE_ORDER": "YMD",
			"PREFER_DAY_OF_MONTH": 'first',
		}
		if data["publish_date"]:
			date = parse_date(data["publish_date"], settings=settings)
			return str(date)[:10]
	return None

def parse_authors(data):
	# TODO: Use get
	if "authors" in data:
		return data['authors']
	return []

def list_authors(authors, as_list=False): # MOVE SOMEWHERE ELSE
	# authors is a list of Author objects
	auth_list = [a.name for a in authors]
	if len(auth_list) > 0:
		if as_list:
			return auth_list
		return ', '.join(auth_list)
	return 'None'

def format_phone_num(phone_num):
	return f'({phone_num[:3]})-{phone_num[3:6]}-{phone_num[6:]}'


def _query_repr(query_obj):
	# Turn query dictionary into user readable string
	qs = [str(q) for q in query_obj.values()]
	return ', '.join(qs)

@app.context_processor
def utility_processor():
	return dict(list_authors=list_authors, format_phone_num=format_phone_num)

@app.before_first_request
def before_first_request():
	# Book.init_index()
	librarian = User.query.filter_by(username='librarian').first()
	if not librarian:
		librarian = User(username='librarian')
		librarian.set_password(app.config['LIBRARIAN_PASSWORD'])
		db.session.add(librarian)
		db.session.commit()


@app.before_request
def before_request():
	if current_user.is_authenticated:
		g.lookUpForm = BookLookUpForm()
		g.enterForm = BookEntryForm()
		g.search_form = SearchForm()
		g.as_form = AdvancedSearchForm()
		g.editForm = BookEditForm()
		g.loanForm = LoanBookForm()
		g.loan_close_form = EmptyForm()
		g.loan_extend_form = LoanExtendForm()

@app.route('/books')
@login_required
def all_books():
	page = request.args.get('page', 1, type=int)
	books = Book.query.order_by(Book.full_title).paginate(page, app.config['BOOKS_PER_PAGE'], False)
	num_books = Book.query.count()
	next_url = url_for('all_books', page=books.next_num) if books.has_next else None
	prev_url = url_for('all_books', page=books.prev_num) if books.has_prev else None
	form = EmptyForm()

	# So that user returns to index page after performing action
	session['url'] = url_for('all_books')

	return render_template('collection.html', form=form, title='Home', num_books=num_books, books=books.items, next_url=next_url, prev_url=prev_url)

@app.route('/')
@app.route('/index')
@login_required
def index():
	loan_phone_form = LoanPhoneForm(request.args)
	if loan_phone_form.validate():
		return redirect(url_for('loanee_history', phone_num=loan_phone_form.phone_num.data))
	page = request.args.get('page', 1, type=int)
	exp_loans_q = Loan.get_expiring(delta=1, unit='weeks')
	exp_loans_pag = exp_loans_q.paginate(page, app.config['LOANS_PER_PAGE'], False)
	exp_loans = {
		'total': exp_loans_q.count(),
		'items': exp_loans_pag.items
	}
	next_url = url_for('index', page=exp_loans_pag.next_num) if exp_loans_pag.has_next else None
	prev_url = url_for('index', page=exp_loans_pag.prev_num) if exp_loans_pag.has_prev else None
	context = {
		'loan_phone_form': loan_phone_form,
		'exp_loans': exp_loans,
		'next_url': next_url,
		'prev_url': prev_url
	}
	# So that user returns to dashboard page after performing action
	session['url'] = url_for('index')
	return render_template('dashboard.html', **context)

@app.route('/login', methods=['GET', 'POST'])
def login():
	if current_user.is_authenticated: # current_user is User object from db
		return redirect(url_for('index'))
	form = LoginForm()
	if form.validate_on_submit():
		user = User.query.filter_by(username=form.username.data).first()
		if user is None or not user.check_password(form.password.data):
			flash('Invalid username or password')
			return redirect(url_for('login'))
		login_user(user, remember=form.remember_me.data)
		next_page = request.args.get('next') # request.args contains query string in dict
		if not next_page or url_parse(next_page).netloc != '': # 2nd cond checks if next_page is absolute path
			next_page = url_for('index')
		return redirect(next_page)
	return render_template('login.html', title='Sign In', form=form)

@app.route('/lookup/<book_isbn>', methods=['GET'])
@login_required
def lookup(book_isbn):
	try:
		val_isbn = isbn.ISBN(book_isbn)
		isbn_10, isbn_13 = val_isbn.isbn10(), val_isbn.isbn13()
	except isbn.ISBNError: # Make this into a function
		payload = {'error': HTTP_STATUS_CODES.get(400, 'Unknown error')}
		payload['message'] = 'must be a valid ISBN-10 or ISBN-13 number'
		response = jsonify(payload)
		response.status_code = 400
		return response

	info_url = f'https://openlibrary.org/isbn/{isbn_13}.json'
	book_info = dict()
	data = None
	try:
		with urllib.request.urlopen(info_url) as response:
			payload = response.read()
			data = json.loads(payload)
		full_title = parse_title(data)
		pages = parse_pages(data)
		publish_date = parse_publish_date(data)
	except HTTPError:
		full_title, pages, publish_date = '','',''
	finally:
		book_info = {
			'isbn_10': isbn_10,
			'isbn_13': isbn_13,
			'full_title': full_title,
			'pages': pages,
			'publish_date': publish_date
		}

	cover_url = f'https://covers.openlibrary.org/b/isbn/{isbn_13}-M.jpg?default=False'
	try:
		response = urllib.request.urlopen(cover_url)
	except HTTPError:
		cover_url = ''
	finally:
		book_info['cover'] = cover_url

	# Get author information
	if not data:
		book_info['authors'] = ''
	else:
		authors = parse_authors(data)
		author_names = []
		for author in authors:
			url = 'https://openlibrary.org{}.json'.format(author['key'])
			with urllib.request.urlopen(url) as response:
				payload = response.read()
				name = json.loads(payload)['name']
			author_names.append(name)
		book_info['authors'] = ', '.join(author_names)

	return jsonify(book_info)

@app.route('/books/', methods=['POST'])
@login_required
def add_to_collection():
	if g.enterForm.validate_on_submit():

		book = Book.query.filter_by(isbn_13=g.enterForm.isbn_13.data).first()
		if book:
			flash(f'Book <ISBN-13 {book.isbn_13}> is already in collection', 'error')
			return redirect(url_for('enter'))
		book = Book(isbn_13=g.enterForm.isbn_13.data, full_title=g.enterForm.full_title.data)

		category = Category.query.filter_by(name=g.enterForm.category.data).first()
		if not category:
			category = Category(name=g.enterForm.category.data)
			db.session.add(category)
			db.session.commit()

		book.isbn_10 = g.enterForm.isbn_10.data # Could be null
		book.pages = g.enterForm.pages.data
		book.publish_date = parse_date(g.enterForm.publish_date.data)
		book.book_category = category
		if g.enterForm.cover.data == '':
			book.cover = url_for('static', filename='nocover.jpg')
		else:
			book.cover = g.enterForm.cover.data

		# ADD AUTHOR HERE
		for author_token in g.enterForm.authors.data.split(', '):
			author_token = author_token.strip().title()
			author = Author.query.filter_by(name=author_token).first()
			if not author:
				author = Author(name=author_token)
				db.session.add(author)
				
			book.authors.append(author)

		db.session.add(book)
		db.session.commit()
		flash(f"Successfully entered {book.full_title} <ISBN-13: {book.isbn_13}> into collection.", 'success')
		return redirect(url_for('enter'))
	return render_template('lookup.html', title='Enter Books')


@app.route('/enter', methods=['GET'])
@login_required
def enter():
	return render_template('lookup.html', title='Enter Books')


@app.route('/books/<book_isbn>', methods=['DELETE'])
@login_required
def delete(book_isbn):
	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'Book <ISBN-13: {book_isbn}> not in collection', 'error')
		payload = {'error': HTTP_STATUS_CODES.get(404, 'Unknown error')}
		payload['message'] = f'Book {book_isbn} not in collection'
		response = jsonify(payload)
		response.status_code = 404
		return response

	if not book.is_on_shelf():
		flash(f'Book <ISBN-13:{book_isbn}> is not on shelf and cannot be deleted. Close the current loan and try again.', 'error')
		payload = {'error': HTTP_STATUS_CODES.get(400, 'Unknown error')}
		payload['message'] = f'Book {book_isbn} is not on shelf and cannot be deleted. Close the current loan and try again.'
		response = jsonify(payload)
		response.status_code = 400
		return response

	db.session.delete(book)
	db.session.commit()
	flash(f'Book <ISBN-13: {book_isbn}> successfully deleted from collection', 'success')
	payload = {
		'message': f"Book {book_isbn} deleted from collection",
		'url': url_for('index')
	}
	return jsonify(payload)


@app.route('/book/<book_isbn>', methods=['POST'])
@login_required
def edit(book_isbn):

	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'Book <ISBN-13: {book_isbn}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if g.editForm.validate_on_submit():
		# Update category
		# If category has changed, look up or add and change it
		if g.editForm.category.data != book.book_category.name:
			category = Category.query.filter_by(name=g.editForm.category.data).first()
			if not category:
				category = Category(name=g.editForm.category.data)
				db.session.add(category)
				db.session.commit()
			book.book_category = category

		# Update authors
		existing_authors = [a.name for a in book.authors]
		new_author_tok = [a.strip().title() for a in g.editForm.authors.data.split(', ')]
		new_authors = []

		if existing_authors != new_author_tok:
			for author_token in new_author_tok:
				author = Author.query.filter_by(name=author_token).first()
				if not author:
					author = Author(name=author_token)
					db.session.add(author)
					db.session.commit()
				new_authors.append(author)
			book.authors = new_authors

		# Update other fields
		book.full_title = g.editForm.full_title.data
		book.pages = g.editForm.pages.data
		book.publish_date = parse_date(g.editForm.publish_date.data)

		db.session.commit()
		flash(f'Successfully changed Book <ISBN-13: {book_isbn}> data', 'success')
	else:
		flash(f'Unable to change Book <ISBN-13: {book_isbn}> data. Try again.', 'error')

	return redirect(url_for('all_books'))





@app.route('/books/authors/<author_name>', methods=['GET'])
@login_required
def search_author(author_name):
	page = request.args.get('page', 1, type=int)
	books, total = Book.search_author(author_name, page, app.config['BOOKS_PER_PAGE'], exact=True)
	next_url = url_for('search_author', author_name=author_name, page=page + 1) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('search_author', author_name=author_name, page=page - 1) if page > 1 else None
	form = EmptyForm()
	return render_template('search.html', 
		search_type='Author',
		search_query=author_name,
		form=form, 
		title='Search', 
		num_books=total, 
		books=books.items, 
		next_url=next_url, 
		prev_url=prev_url
		)

@app.route('/books/advanced', methods=['GET', 'POST'])
@login_required
def advanced_search():
	if not g.as_form.validate_on_submit():
		print(g.as_form.errors.items())
		flash('Unable to query via advanced search. Check that at least one field is not empty and try again.', 'error')
		return redirect(url_for('all_books'))

	page = request.args.get('page', 1, type=int)

	query_obj = dict()
	if g.as_form.full_title.data:
		query_obj['full_title'] = g.as_form.full_title.data
	if g.as_form.authors.data:
		query_obj['authors'] = g.as_form.authors.data
	if g.as_form.category.data and g.as_form.category.data != 'None':
		query_obj['category'] = g.as_form.category.data
	if g.as_form.publish_date.data:
		query_obj['publish_date'] = g.as_form.publish_date.data

	if len(query_obj) == 0:
		flash('At least one field must be non-empty for advanced search.', 'error')
		return redirect(url_for('all_books'))

	books, total = Book.search_advanced(query_obj, page, app.config['BOOKS_PER_PAGE'])
	next_url = url_for('advanced_search', page=page + 1) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('advanced_search', page=page - 1) if page > 1 else None
	form = EmptyForm()

	# So that user returns to index page after performing action
	session['url'] = url_for('all_books')

	context = {
		'search_type':'Advanced',
		'search_query':_query_repr(query_obj),
		'form':form,
		'num_books':total,
		'books':books.items,
		'next_url':next_url,
		'prev_url':prev_url,
	}
	return render_template('search.html', **context)

		
@app.route('/search', methods=['GET', 'POST'])
@login_required
def search():
	if not g.search_form.validate_on_submit():
		print(g.search_form.errors.items())
		return redirect(url_for('all_books'))
	page = request.args.get('page', 1, type=int)
	if g.search_form.search_type.data == 'Keyword':
		books, total = Book.search_keyword(g.search_form.q.data, page, app.config['BOOKS_PER_PAGE'])
	elif g.search_form.search_type.data == 'Title':
		books, total = Book.search_title(g.search_form.q.data, page, app.config['BOOKS_PER_PAGE'])
	else:
		books, total = Book.search_author(g.search_form.q.data, page, app.config['BOOKS_PER_PAGE'])
	next_url = url_for('search', q=g.search_form.q.data, page=page + 1) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('search', q=g.search_form.q.data, page=page - 1) if page > 1 else None
	form = EmptyForm()

	# So that user returns to index page after performing action
	session['url'] = url_for('index')

	return render_template('search.html', 
		search_type=g.search_form.search_type.data,
		search_query=g.search_form.q.data,
		form=form, 
		title='Search', 
		num_books=total, 
		books=books.all(), 
		next_url=next_url, 
		prev_url=prev_url
		)

@app.route('/loan/<book_isbn>', methods=['POST'])
@login_required
def loan(book_isbn):
	if not g.loanForm.validate_on_submit():
		print(g.search_form.errors.items())
		flash(f'Unable to check out Book <ISBN-13: {book_isbn}> for lending. Try again.', 'error')
		return redirect(url_for('all_books'))

	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'Book <ISBN-13{book_isbn}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if not book.is_on_shelf():
		flash(f'Book <ISBN-13: {book_isbn}> is not available for lending.', 'error')
		return redirect(url_for('all_books'))

	# Create a loan object
	loan_obj = Loan(loaned_book=book)
	loan_obj.phone_num = g.loanForm.phone_num.data
	loan_obj.loanee = g.loanForm.name.data
	td = timedelta(**{g.loanForm.loan_duration_unit.data: g.loanForm.loan_duration_length.data})
	loan_obj.in_timestamp = datetime.today().date() + td

	db.session.add(loan_obj)
	db.session.commit()
	flash(f'Book <ISBN-13: {book_isbn}> successfully checked out to {g.loanForm.name.data} ({g.loanForm.phone_num.data})', 'success')
	return redirect(url_for('index'))

@app.route('/loan/extend/<book_isbn>', methods=['POST'])
@login_required
def extend_loan(book_isbn):
	if not g.loan_extend_form.validate_on_submit():
		flash(f'Unable to process loan extention for Book <ISBN-13: {book_isbn}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'404: Book <ISBN-13: {book_isbn}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if book.is_on_shelf():
		flash(f'400: Book <ISBN-13: {book_isbn}> is currently on shelf and not available for loan extension.', 'error')
		return redirect(url_for('all_books'))

	loan = book.get_current_loan()
	success = loan.extend_loan_period(g.loan_extend_form.loan_duration_length.data, g.loan_extend_form.loan_duration_unit.data)

	if not success:
		flash(f'500: Unable to extend loan for Book <ISBN-13: {book_isbn}>. Contact site administrator.', 'error')

	db.session.commit()
	flash(f'Book <ISBN-13: {book_isbn}> loan successfully extended for {g.loan_extend_form.loan_duration_length.data} {g.loan_extend_form.loan_duration_unit.data}.', 'success')

	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('all_books'))

@app.route('/loan/close/<book_isbn>', methods=['POST'])
@login_required
def close_loan(book_isbn):
	if not g.loan_close_form.validate_on_submit():
		flash(f'Unable to process loan extention for Book <ISBN-13: {book_isbn}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'404: Book <ISBN-13: {book_isbn}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if book.is_on_shelf():
		flash(f'400: Book <ISBN-13: {book_isbn}> is currently on shelf and has no loan attached.', 'error')
		return redirect(url_for('all_books'))

	loan = book.get_current_loan()
	success = loan.close()

	if not success:
		flash(f'500: Unable to close loan for Book <ISBN-13: {book_isbn}>. Contact site administrator.', 'error')

	db.session.commit()
	flash(f'Book <ISBN-13: {book_isbn}> loan was successfully closed and is back on the shelf.', 'success')

	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('index'))

@app.route('/book/loans/<book_isbn>', methods=['GET'])
@login_required
def book_history(book_isbn):
	book = None
	if len(book_isbn) == 10:
		book = Book.query.filter_by(isbn_10=book_isbn).first()
	elif len(book_isbn) == 13:
		book = Book.query.filter_by(isbn_13=book_isbn).first()

	if book is None:
		flash(f'404: Book <ISBN-13: {book_isbn}> not in collection', 'error')
		abort(404)

	page = request.args.get('page', 1, type=int)
	curr_loan = book.get_current_loan()
	past_loans_q = book.loans.filter_by(returned=True).order_by(Loan.in_timestamp)
	past_loans_count = past_loans_q.count()
	past_loans = past_loans_q.all()
	# next_url = url_for('book_history', book_isbn=book_isbn, page=past_loans.next_num) if past_loans.has_next else None
	# prev_url = url_for('book_history', book_isbn=book_isbn, page=past_loans.prev_num) if past_loans.has_prev else None

	context = {
		'book': book,
		'curr_loan': curr_loan,
		'past_loans': {'count': past_loans_count, 'items': past_loans}
		# 'next_url': next_url,
		# 'prev_url': prev_url,
		# 'order_url': order_url,
		# 'ascending': asc
	}

	return render_template('book_history.html', **context)

@app.route('/loanee/<phone_num>', methods=['GET'])
@login_required
def loanee_history(phone_num):
	all_loans = Loan.get_loans(phone_num)
	count = all_loans.count()

	if count == 0: # add server-side validation and change to 404
		flash(f'404: User with phone number ({phone_num}) not found in records', 'error')
		return redirect(url_for('dashboard'))

	page = request.args.get('page', 1, type=int)
	curr_loans_q = all_loans.filter_by(returned=False)
	curr_loans = {'count': curr_loans_q.count(), 'items': curr_loans_q.all()}
	past_loans_q = all_loans.filter_by(returned=True)
	past_loans_pag = past_loans_q.paginate(page, app.config['BOOKS_PER_PAGE'], False)
	past_loans = {
		'count': past_loans_q.count(),
		'items': past_loans_pag.items
	}
	next_url = url_for('loanee_history', phone_num=phone_num, page=past_loans_pag.next_num) if past_loans_pag.has_next else None
	prev_url = url_for('loanee_history', phone_num=phone_num, page=past_loans_pag.prev_num) if past_loans_pag.has_prev else None

	# So that user returns to same loanee info page after performing action
	session['url'] = url_for('loanee_history', phone_num=phone_num)

	context = {
		'phone_num': phone_num,
		'curr_loans': curr_loans,
		'past_loans': past_loans,
		'next_url': next_url,
		'prev_url': prev_url
	}


	return render_template('loanee_history.html', **context)


@app.route('/loans', methods=['GET'])
@login_required
def loans():
	exp_loans_q = Loan.get_expiring_loans()
	page = request.args.get('page', 1, type=int)
	exp_loans_pag = exp_loans_q.paginate(page, app.config['BOOKS_PER_PAGE'], False)
	exp_loans = {
		'count': exp_loans_q.count(),
		'items': exp_loans_pag.items
	}
	next_url = url_for('loans', page=exp_loans_pag.next_num) if exp_loans_pag.has_next else None
	prev_url = url_for('loans', page=exp_loans_pag.prev_num) if exp_loans_pag.has_prev else None

	context = {
		'exp_loans': exp_loans,
		'next_url': next_url,
		'prev_url': prev_url
	}
	return render_template('loans_home.html', **context)


'''
To get a list of all items
	GET /<noun-plural>
	GET /books

To get one of the items
	GET /<noun-plural>/:id
	GET /books/1

To create a new item of type
	POST /<noun-plural>
	POST /books

To edit one item
	POST /<noun-plural>/:id
	POST /books/1

To delete one item
	DELETE /<noun-plural>/:id
	DELETE /books/1
'''



@app.route('/logout')
def logout():
	logout_user()
	return redirect(url_for('login'))
