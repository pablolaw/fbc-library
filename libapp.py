from app import app, db
from app.models import User, Category, Author, Book, BookIndex, Loan
from datetime import date

@app.shell_context_processor
def make_shell_context():
	return {
		'db': db,
		'User': User,
		'Category': Category,
		'Author': Author,
		'Book': Book,
		'Loan': Loan,
		'date': date,
		'BookIndex': BookIndex
	}