from flask import render_template, flash, redirect, url_for, request, session, g, jsonify, abort
from app import db, app
from app.forms import LoginForm, BookLookUpForm, BookEditForm, BookEntryForm, EmptyForm, SearchForm, AdvancedSearchForm, LoanBookForm, LoanExtendForm, LoanPhoneForm
from flask_login import current_user, login_user, logout_user, login_required
from app.models import User, Book, Category, Author, Loan, Copy, Loanee
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

def delete_copies_from_collection(book: Book, new_total: int) -> int:
	num_existing = book.num_total_copies()
	print(f'Book currently has {num_existing} copies in collection.')
	if num_existing > new_total:
		# Check if there are enough copies to delete
		num_delete = num_existing - new_total
		if book.num_available_copies() >= num_delete:
			to_delete = book.delete_copies(num_delete)
			if len(to_delete) == 0:
				flash(f'500: Unable to delete {num_delete} copies of Book <{book.full_title}>', 'error')
				return 0
			else:
				for id in to_delete: # Can't do a bulk delete because of cascade relationship
					copy = Copy.query.get_or_404(int(id))
					db.session.delete(copy)
				return -num_delete
		else:
			flash(f"400: Can't delete more copies of Book <{book.full_title}> than are available.", 'error')
	return 0


def add_copies_to_collection(book: Book, new_total: int) -> int:
	num_existing = book.num_total_copies()
	if num_existing < new_total and new_total <= app.config['MAX_NUMBER_OF_COPIES']:
		num_create = new_total - num_existing
		_ = book.generate_copies(num_create)
		return num_create
	flash(f"400: Total number of copies exceeds max number of copies. No new copies of Book <{book.full_title}> were created", 'error')
	return 0



@app.context_processor
def utility_processor():
	return dict(list_authors=list_authors, format_phone_num=format_phone_num)

@app.before_first_request
def before_first_request():
	# Book.init_index()
	librarian = User.query.filter_by(username='librarian').first()
	commit = False
	if not librarian:
		librarian = User(username='librarian')
		librarian.set_password(app.config['LIBRARIAN_PASSWORD'])
		db.session.add(librarian)
		commit = True
	
	# Add 'MISSING' category
	missing = Category.query.filter_by(name='MISSING').first()
	if not missing:
		missing = Category(name='MISSING')
		db.session.add(missing)
		commit = True
	
	if commit:
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
	loanees = Loanee.query.order_by(Loanee.name).all()
	categories = Category.query.order_by(Category.name).all()

	# So that user returns to index page after performing action
	session['url'] = url_for('all_books')

	context = {
		'book_categories': categories,
		'all_loanees': loanees,
		'form': form,
		'title': 'Collection',
		'num_books': num_books,
		'books': books.items,
		'next_url': next_url,
		'prev_url': prev_url
	}

	return render_template('collection.html', **context)

@app.route('/')
@app.route('/index')
@login_required
def index():
	loan_phone_form = LoanPhoneForm(request.args)
	if loan_phone_form.validate():
		return redirect(url_for('loanee_history', q_type=loan_phone_form.search_type.data, q=loan_phone_form.q.data))
	page = request.args.get('page', 1, type=int)
	exp_loans_q = Loan.get_expiring(delta=1, unit='weeks')
	exp_loans_pag = exp_loans_q.paginate(page, app.config['LOANS_PER_PAGE'], False)
	exp_loans = {
		'total': exp_loans_q.count(),
		'items': exp_loans_pag.items
	}
	next_url = url_for('index', page=exp_loans_pag.next_num) if exp_loans_pag.has_next else None
	prev_url = url_for('index', page=exp_loans_pag.prev_num) if exp_loans_pag.has_prev else None
	loanees = Loanee.query.order_by(Loanee.name).all()

	context = {
		'loanees': loanees,
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
		status_code = 200
	except HTTPError:
		full_title, pages, publish_date = '','',''
		status_code = 400
	finally:
		book_info = {
			'isbn_10': isbn_10,
			'isbn_13': isbn_13,
			'full_title': full_title,
			'pages': pages,
			'publish_date': publish_date,
			'status_code': status_code
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
		book = Book(full_title=g.enterForm.full_title.data)

		if g.enterForm.isbn_13.data:
			q_book = Book.get_by_isbn(g.enterForm.isbn_13.data)
			if q_book:
				flash(f'Book <ISBN-13 {q_book.isbn_13}> is already in collection', 'error')
				return redirect(url_for('enter'))
			book.isbn_13 = g.enterForm.isbn_13.data

		category = None
		if g.enterForm.category.data == '':
			category = Category.query.filter_by(name='MISSING').first()
		else:
			category = Category.query.filter_by(name=g.enterForm.category.data).first()
			if not category:
				category = Category(name=g.enterForm.category.data)
				db.session.add(category)
				db.session.commit()

		book.isbn_10 = g.enterForm.isbn_10.data # Could be null
		book.pages = g.enterForm.pages.data
		book.publish_date = parse_date(g.enterForm.publish_date.data)
		book.book_category = category
		
		if g.enterForm.number_of_copies.data > 0 and g.enterForm.number_of_copies.data < app.config['MAX_NUMBER_OF_COPIES']:
			num_copies = g.enterForm.number_of_copies.data
		else:
			num_copies = 1
		_ = book.generate_copies(num_copies)

		if g.enterForm.cover.data == '':
			book.cover = url_for('static', filename='nocover.jpg')
		else:
			book.cover = g.enterForm.cover.data

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
	categories = Category.query.order_by(Category.name).all()
	return render_template('lookup.html', book_categories=categories, title='Enter Books')


@app.route('/books/<book_id>', methods=['DELETE'])
@login_required
def delete(book_id):
	book = Book.query.get(int(book_id))
	print(f'The Book: {book}')
	if book is None:
		flash(f'Book <id: {book_id}> not in collection', 'error')
		payload = {'error': HTTP_STATUS_CODES.get(404, 'Unknown error')}
		payload['message'] = f'Book {book_id} not in collection'
		response = jsonify(payload)
		response.status_code = 404
		return response

	if not book.all_copies_available():
		flash(f'Book <id:{book_id}> has copies not on shelf and cannot be deleted. Close any current loans and try again.', 'error')
		payload = {'error': HTTP_STATUS_CODES.get(400, 'Unknown error')}
		payload['message'] = f'Book <id:{book_id}> has copies not on shelf and cannot be deleted. Close any current loans and try again.'
		response = jsonify(payload)
		response.status_code = 400
		return response

	book_title = book.full_title
	db.session.delete(book)
	db.session.commit()
	flash(f'Book <{book_title}> successfully deleted from collection', 'success')
	payload = {
		'message': f"Book <{book_title}> deleted from collection",
		'url': url_for('index')
	}
	return jsonify(payload)


@app.route('/book/<book_id>', methods=['POST'])
@login_required
def edit(book_id):

	book = Book.query.get_or_404(int(book_id))
	if book is None:
		flash(f'Book <id: {book_id}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if g.editForm.validate_on_submit():
		# Add or delete copies
		num_created = 0
		num_existing = book.num_total_copies()
		if num_existing < g.editForm.number_of_copies.data:
			num_created = add_copies_to_collection(book, g.editForm.number_of_copies.data)
		elif g.editForm.number_of_copies.data < num_existing:
			num_created = delete_copies_from_collection(book, g.editForm.number_of_copies.data)
			
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
		flash(f'Successfully changed Book <{book.full_title}> data', 'success')
		if num_created > 0:
			flash(f'{num_created} copies of Book <{book.full_title}> were added to the collection.', 'info')
		elif num_created < 0:
			flash(f'{-num_created} copies of Book <{book.full_title}> were deleted from the collection.', 'info')
	else:
		if 'number_of_copies' in g.editForm.errors:
			max_copies = app.config['MAX_NUMBER_OF_COPIES']
			flash(f'400: Total number of copies in collection must be between 1 and {max_copies}.', 'error')
		else:
			flash(f'Unable to change Book <{book.full_title}> data. Try again.', 'error')

	return redirect(url_for('all_books'))


@app.route('/books/authors/<author_name>', methods=['GET'])
@login_required
def search_author(author_name):
	page = request.args.get('page', 1, type=int)
	books, total = Book.search_author(author_name, page, app.config['BOOKS_PER_PAGE'], exact=True)
	next_url = url_for('search_author', author_name=author_name, page=page + 1) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('search_author', author_name=author_name, page=page - 1) if page > 1 else None
	form = EmptyForm()
	loanees = Loanee.query.order_by(Loanee.name).all()
	categories = Category.query.order_by(Category.name).all()

	return render_template('search.html',
		book_categories= categories,
		all_loanees= loanees, 
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
	g.as_form.category.choices = [(c.name, c.name) for c in Category.query.order_by('name')]
	if request.method == 'POST' and not g.as_form.validate_on_submit():
		print(g.as_form.errors.items())
		flash('Unable to query via advanced search. Check that at least one field is not empty and try again.', 'error')
		return redirect(url_for('all_books'))

	query_obj = dict()
	page = request.args.get('page', 1, type=int)

	if request.method == 'GET':
		if 'title' in request.args:
			query_obj['full_title'] = request.args.get('title')
		if 'authors' in request.args:
			query_obj['authors'] = request.args.get('authors')
		if 'category' in request.args:
			query_obj['category'] = request.args.get('category')
		if 'publish_date' in request.args:
			query_obj['publish_date'] = request.args.get('publish_date')
	else:
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
	next_url = url_for('advanced_search', page=page + 1, **query_obj) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('advanced_search', page=page - 1, **query_obj) if page > 1 else None
	form = EmptyForm()
	loanees = Loanee.query.order_by(Loanee.name).all()
	categories = Category.query.order_by(Category.name).all()

	# So that user returns to index page after performing action
	session['url'] = url_for('all_books')

	context = {
		'book_categories': categories,
		'all_loanees': loanees,
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
	if request.method == 'POST' and not g.search_form.validate_on_submit():
		return redirect(url_for('all_books'))
	page = request.args.get('page', 1, type=int)

	if request.method == 'GET':
		search_type = request.args.get('search_type')
		query = request.args.get('q')
		if search_type is None:
			flash(f'400: Search type is missing in query.', 'error')
			return redirect(url_for('all_books'))
		if query is None:
			flash(f'400: Query string is missing in query.', 'error')
			return redirect(url_for('all_books'))
	else:
		search_type = g.search_form.search_type.data
		query = g.search_form.q.data

	if search_type == 'Keyword':
		books, total = Book.search_keyword(query, page, app.config['BOOKS_PER_PAGE'])
	elif search_type == 'Title':
		books, total = Book.search_title(query, page, app.config['BOOKS_PER_PAGE'])
	else:
		books, total = Book.search_author(query, page, app.config['BOOKS_PER_PAGE'])

	next_url = url_for('search', search_type=search_type, q=query, page=page + 1) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('search', search_type=search_type, q=query, page=page - 1) if page > 1 else None
	form = EmptyForm()
	loanees = Loanee.query.order_by(Loanee.name).all()
	categories = Category.query.order_by(Category.name).all()

	# So that user returns to index page after performing action
	session['url'] = url_for('index')

	return render_template('search.html',
		book_categories= categories,
		all_loanees= loanees, 
		search_type=g.search_form.search_type.data,
		search_query=g.search_form.q.data,
		form=form, 
		title='Search', 
		num_books=total, 
		books=books.all(), 
		next_url=next_url, 
		prev_url=prev_url
		)

@app.route('/loan/<book_id>', methods=['POST'])
@login_required
def loan(book_id):
	if not g.loanForm.validate_on_submit():
		flash(f'Unable to check out Book <id: {book_id}> for lending. Try again.', 'error')
		return redirect(url_for('all_books'))

	book = Book.query.get(int(book_id))
	if book is None:
		flash(f'Book <id: {book_id}> not in collection', 'error')
		return redirect(url_for('all_books'))

	if book.num_available_copies() == 0:
		flash(f'400: Book <id: {book_id}> is not available for lending.', 'error')
		return redirect(url_for('all_books'))

	# Create or get a loanee object
	if g.loanForm.phone_num.data:
		loanee = Loanee.query.filter_by(name=g.loanForm.name.data).first()
	else:
		loanee = Loanee.query.filter(Loanee.name==g.loanForm.name.data, Loanee.phone_num == g.loanForm.phone_num.data).first()
	if loanee is None:
		loanee = Loanee(phone_num=g.loanForm.phone_num.data, name=g.loanForm.name.data)
		db.session.add(loanee)

	# Create loan object and attach it and loanee to book copy
	td = timedelta(**{g.loanForm.loan_duration_unit.data: g.loanForm.loan_duration_length.data})
	loan_obj = Loan(in_timestamp=datetime.today().date() + td)
	book.take_out_loan(loan_obj, loanee)

	db.session.add(loan_obj)
	db.session.commit()
	flash(f'Book <id: {book_id}> successfully checked out to {g.loanForm.name.data}', 'success')
	return redirect(url_for('index'))

@app.route('/loan/extend/<loan_id>', methods=['POST'])
@login_required
def extend_loan(loan_id):
	if not g.loan_extend_form.validate_on_submit():
		flash(f'400: Unable to process loan extention for Loan <id: {loan_id}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	loan = Loan.query.get(int(loan_id))

	if loan is None:
		flash(f'404: Loan <id: {loan_id}> not found.', 'error')
		return redirect(url_for('all_books'))

	if not loan.is_active():
		flash(f'400: Loan <id: {loan_id}> has already been closed and cannot be extended.', 'error')

	success = loan.extend_loan_period(g.loan_extend_form.loan_duration_length.data, g.loan_extend_form.loan_duration_unit.data)

	if not success:
		flash(f'500: Unable to extend Loan <id: {loan_id}>. Contact site administrator.', 'error')
		return redirect(url_for('all_books'))

	db.session.commit()
	flash(f'Loan <to: {loan.loaning_person.name or loan.loaning_person.phone_num}> successfully extended for {g.loan_extend_form.loan_duration_length.data} {g.loan_extend_form.loan_duration_unit.data}.', 'success')

	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('all_books'))

@app.route('/loans/close/<loan_id>', methods=['POST'])
@login_required
def close_loan(loan_id):
	if not g.loan_close_form.validate_on_submit():
		flash(f'400: Unable to close Loan <id: {loan_id}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	loan = Loan.query.get(int(loan_id))

	if loan is None:
		flash(f'404: Loan <id: {loan_id}> not found.', 'error')
		return redirect(url_for('all_books'))

	if not loan.is_active():
		flash(f'400: Loan <id: {loan_id}> has already been closed and cannot be closed again.', 'error')

	success = loan.close()

	if not success:
		flash(f'500: Unable to close Loan <id: {loan_id}>. Contact site administrator.', 'error')

	db.session.commit()
	flash(f'Loan <to: {loan.loaning_person.name or loan.loaning_person.phone_num}> was successfully closed and the book is back on the shelf.', 'success')
	
	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('index'))

@app.route('/book/loans/<book_id>', methods=['GET'])
@login_required
def book_history(book_id):
	book = Book.query.get(int(book_id))

	if book is None:
		flash(f'404: Book <id: {book_id}> not in collection', 'error')
		abort(404)

	# page = request.args.get('page', 1, type=int)
	all_loans = book.get_all_loans()
	curr_loan = all_loans['current']
	past_loans = all_loans['past']
	# next_url = url_for('book_history', book_isbn=book_isbn, page=past_loans.next_num) if past_loans.has_next else None
	# prev_url = url_for('book_history', book_isbn=book_isbn, page=past_loans.prev_num) if past_loans.has_prev else None

	context = {
		'book': book,
		'curr_loans': {'count': curr_loan[0], 'items': curr_loan[1]},
		'past_loans': {'count': past_loans[0], 'items': past_loans[1]}
		# 'next_url': next_url,
		# 'prev_url': prev_url,
		# 'order_url': order_url,
		# 'ascending': asc
	}

	return render_template('book_history.html', **context)

@app.route('/loanee/<q_type>/<q>', methods=['GET'])
@login_required
def loanee_history(q_type, q):
	if q_type == 'Name':
		loanee = Loanee.get_by_name(q)
	elif q_type == 'Phone':
		loanee = Loanee.get_by_phone(q)
	else:
		flash(f"400: Invalid loanee query type '{q_type}'. Try again with either 'Name', or 'Phone'.", 'error')
		return redirect(url_for('index'))

	if not loanee: # add server-side validation and change to 404
		flash(f'404: User with {q_type}: {q} not found in records', 'error')
		return redirect(url_for('index'))

	all_loans = loanee.loans
	page = request.args.get('page', 1, type=int)
	curr_loans_q = all_loans.filter_by(returned=False)
	curr_loans = {'count': curr_loans_q.count(), 'items': curr_loans_q.all()}
	past_loans_q = all_loans.filter_by(returned=True)
	past_loans_pag = past_loans_q.paginate(page, app.config['BOOKS_PER_PAGE'], False)
	past_loans = {
		'count': past_loans_q.count(),
		'items': past_loans_pag.items
	}
	# So that user returns to same loanee info page after performing action
	session['url'] = url_for('loanee_history', q_type=q_type, q=q)

	context = {
		'loanee': loanee,
		'curr_loans': curr_loans,
		'past_loans': past_loans,
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
