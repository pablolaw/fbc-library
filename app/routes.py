from email.policy import default
from typing import Any
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
from functools import wraps
from app.search import SearchBuilder
from app.book_util import BookBuilder, BookInfoBuilder
from isbn import ISBNError

# UTILITY FUNCTIONS (should probably put these somewhere else...)

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


def entity_exists(entity_cls):
	def decorator(f):
		@wraps(f)
		def wrapper(*args, **kwargs):
			entity_id = None
			entity_key = None
			for k in kwargs.keys():
				if k.endswith('_id'):
					entity_id = kwargs[k]
					entity_key = k
			if entity_id is None:
				abort(400, f'Request is missing an identifier for {entity_cls.__name__}')

			if issubclass(entity_cls, db.Model):
				entity = entity_cls.query.get(entity_id)
				if entity is None:
					abort(404, f'<{entity_cls.__name__} id: {entity_id}> does not exist in collection.')
				kwargs[entity_cls.__name__.lower()] = entity
				kwargs.pop(entity_key)
			return f(*args, **kwargs)
		return wrapper
	return decorator


@app.errorhandler(404)
def not_found_error(error):
	if error.description:
		flash(error.description, 'error')
		return index(), 404
	return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(error):
    return render_template('500.html'), 500



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
		'page_type': 'view',
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
		info_builder = BookInfoBuilder(book_isbn)
	except ISBNError:
		payload = {'error': HTTP_STATUS_CODES.get(400, 'Unknown error')}
		payload['message'] = 'must be a valid ISBN-10 or ISBN-13 number'
		response = jsonify(payload)
		response.status_code = 400
		return response

	book_info = info_builder.get_info() \
	                        .get_cover() \
							.get_authors() \
							.data()

	return jsonify(book_info)

@app.route('/books/', methods=['POST'])
@login_required
def add_to_collection():
	if g.enterForm.validate_on_submit():
		builder = BookBuilder()
		book = builder.enter_title(g.enterForm.full_title.data) \
			          .enter_isbn13(g.enterForm.isbn_13.data) \
					  .enter_isbn10(g.enterForm.isbn_10.data) \
					  .enter_category(g.enterForm.category.data) \
					  .enter_pages(g.enterForm.pages.data) \
					  .enter_publish_date(g.enterForm.publish_date.data) \
					  .enter_num_copies(g.enterForm.number_of_copies.data, app.config['MAX_NUMBER_OF_COPIES']) \
					  .enter_cover(g.enterForm.cover.data) \
					  .enter_authors(g.enterForm.authors.data).book()

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


@app.route('/books/<int:book_id>', methods=['DELETE'])
@entity_exists(Book)
@login_required
def delete(book: Book) -> Any:

	if not book.all_copies_available():
		flash(f'Book <id:{book.id}> has copies not on shelf and cannot be deleted. Close any current loans and try again.', 'error')
		payload = {'error': HTTP_STATUS_CODES.get(400, 'Unknown error')}
		payload['message'] = f'Book <id:{book.id}> has copies not on shelf and cannot be deleted. Close any current loans and try again.'
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


@app.route('/book/<int:book_id>', methods=['POST'])
@entity_exists(Book)
@login_required
def edit(book: Book) -> Any:

	if g.editForm.validate_on_submit():
		editor = BookBuilder(book)
		to_delete, num_changed = editor.edit_num_copies(g.editForm.number_of_copies.data, app.config['MAX_NUMBER_OF_COPIES'])
		if num_changed < 0:
			for id in to_delete: # Need to do this for cascade delete to work
				copy = Copy.query.get(int(id))
				db.session.delete(copy)

		book = editor.edit_category(g.editForm.category.data) \
			         .edit_authors(g.editForm.authors.data) \
					 .edit_title(g.editForm.full_title.data) \
					 .edit_pages(g.editForm.pages.data) \
					 .edit_publish_date(g.editForm.publish_date.data) \
					 .book()
		
		db.session.add(book)			
		db.session.commit()
		flash(f'Successfully changed Book <{book.full_title}> data', 'success')
		if num_changed > 0:
			flash(f'{num_changed} copies of Book <{book.full_title}> were added to the collection.', 'info')
		elif num_changed < 0:
			flash(f'{-num_changed} copies of Book <{book.full_title}> were deleted from the collection.', 'info')
	else:
		if 'number_of_copies' in g.editForm.errors:
			max_copies = app.config['MAX_NUMBER_OF_COPIES']
			flash(f'400: Total number of copies in collection must be between 1 and {max_copies}.', 'error')
		else:
			flash(f'Unable to change Book <{book.full_title}> data. Try again.', 'error')

	return redirect(session.get('url')) or redirect(url_for('all_books'))


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

	session['url'] = url_for('search_author', author_name=author_name, page=page)

	return render_template('collection.html',
		page_type='search',
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

@app.route('/books/advanced', methods=['GET'])
@login_required
def advanced_search():
	as_form = AdvancedSearchForm(request.args)
	as_form.category.choices = [None] + [c.name for c in Category.query.order_by('name')]
	if not as_form.validate():
		flash('Unable to query via advanced search. Check that at least one field is not empty and try again.', 'error')
		return redirect(url_for('all_books'))

	page = request.args.get('page', 1, type=int)
	builder = SearchBuilder()
	books, total = builder.add_fuzzy_title(request.args.get('full_title')) \
	                      .add_fuzzy_author(request.args.get('authors')) \
					      .add_category(request.args.get('category')) \
					      .add_date(request.args.get('publish_date')) \
					      .search().execute(page, app.config['BOOKS_PER_PAGE'])

	query_args = request.args.to_dict()
	query_args.pop('page', 1)
	query_args.pop('csrf_token', '')

	next_url = url_for('advanced_search', page=page + 1, **query_args) if total > page * app.config['BOOKS_PER_PAGE'] else None
	prev_url = url_for('advanced_search', page=page - 1, **query_args) if page > 1 else None
	form = EmptyForm()
	loanees = Loanee.query.order_by(Loanee.name).all()
	categories = Category.query.order_by(Category.name).all()

	# So that user returns to index page after performing action
	session['url'] = url_for('advanced_search', page=page, **query_args)

	context = {
		'page_type': 'search',
		'book_categories': categories,
		'all_loanees': loanees,
		'search_type':'Advanced',
		'search_query': builder.search_repr(),
		'form':form,
		'num_books':total,
		'books':books.items,
		'next_url':next_url,
		'prev_url':prev_url,
	}
	return render_template('collection.html', **context)

		
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
	session['url'] = url_for('search', search_type=search_type, q=query, page=page)

	return render_template('collection.html',
		page_type='search',
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

@app.route('/loan/<int:book_id>', methods=['POST'])
@entity_exists(Book)
@login_required
def loan(book: Book):
	if not g.loanForm.validate_on_submit():
		flash(f'Unable to check out Book <{book.full_title}> for lending. Try again.', 'error')
		return redirect(url_for('all_books'))

	if book.num_available_copies() == 0:
		flash(f'400: Book <{book.full_title}> is not available for lending.', 'error')
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
	flash(f'Book <{book.full_title}> successfully checked out to {g.loanForm.name.data}', 'success')
	return redirect(url_for('index'))

@app.route('/loan/extend/<int:loan_id>', methods=['POST'])
@entity_exists(Loan)
@login_required
def extend_loan(loan: Loan):
	if not g.loan_extend_form.validate_on_submit():
		flash(f'400: Unable to process loan extention for Loan <id: {loan.id}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	if not loan.is_active():
		flash(f'400: Loan <id: {loan.id}> has already been closed and cannot be extended.', 'error')

	success = loan.extend_loan_period(g.loan_extend_form.loan_duration_length.data, g.loan_extend_form.loan_duration_unit.data)

	if not success:
		flash(f'500: Unable to extend Loan <id: {loan.id}>. Contact site administrator.', 'error')
		return redirect(url_for('all_books'))

	db.session.commit()
	flash(f'Loan <to: {loan.loaning_person.name or loan.loaning_person.phone_num}> successfully extended for {g.loan_extend_form.loan_duration_length.data} {g.loan_extend_form.loan_duration_unit.data}.', 'success')

	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('all_books'))

@app.route('/loans/close/<loan_id>', methods=['POST'])
@entity_exists(Loan)
@login_required
def close_loan(loan: Loan):
	if not g.loan_close_form.validate_on_submit():
		flash(f'400: Unable to close Loan <id: {loan.id}>. Try again.', 'error')
		return redirect(url_for('all_books'))

	if not loan.is_active():
		flash(f'400: Loan <id: {loan.id}> has already been closed and cannot be closed again.', 'error')

	success = loan.close()

	if not success:
		flash(f'500: Unable to close Loan <id: {loan.id}>. Contact site administrator.', 'error')

	db.session.commit()
	flash(f'Loan <to: {loan.loaning_person.name or loan.loaning_person.phone_num}> was successfully closed and the book is back on the shelf.', 'success')
	
	if 'url' in session:
		ret_url = session['url']
		session.pop('url')
		return redirect(ret_url)

	return redirect(url_for('index'))

@app.route('/book/loans/<int:book_id>', methods=['GET'])
@entity_exists(Book)
@login_required
def book_history(book: Book) -> str:

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
