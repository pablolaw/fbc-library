from flask import request
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, IntegerField, DateField, SelectField, HiddenField
from wtforms.validators import DataRequired, Length, NumberRange, ValidationError, Optional
from dateparser import parse as parse_date
from app import app
from isbn import ISBN, isbn

def isbn_validator(form, field):
	try:
		ISBN(field.data)
	except isbn.ISBNError:
		raise ValidationError('Field must be a valid 10 or 13 digit ISBN')

def date_validator(form, field):
	if field.data and not parse_date(field.data):
		raise ValidationError('Field must contain a date')

class SearchForm(FlaskForm):
	search_type = SelectField('Search Type', choices=['Keyword', 'Title', 'Author'], default='Keyword')
	q = StringField('Search', validators=[DataRequired()])
	# submit = SubmitField('Search')

	# def __init__(self, *args, **kwargs):
	# 	if 'formdata' not in kwargs:
	# 		kwargs['formdata'] = request.args
	# 	# if 'csrf_enabled' not in kwargs:
	# 	# 	kwargs['csrf_enabled'] = False
	# 	super(SearchForm, self).__init__(*args, **kwargs)


class AdvancedSearchForm(FlaskForm):
	full_title = StringField('Full Title', validators=[Optional(), Length(min=0, max=100)])
	authors = StringField('Authors', validators=[Optional()])
	publish_date = IntegerField('Year of Publication', validators=[Optional()])
	category = SelectField('Category', choices=[None], default=None, validators=[Optional()])

	def validate(self, extra_validators=None):
		if super(AdvancedSearchForm, self).validate(extra_validators):
			for field in self:
				if field.data:
					return True
			self.full_title.errors.append('At least one field must be not empty')
			return False
		return False


class LoginForm(FlaskForm):
	username = StringField('Username', validators=[DataRequired()])
	password = PasswordField('Password', validators=[DataRequired()])
	remember_me = BooleanField('Remember Me')
	submit = SubmitField('Sign In')

class BookLookUpForm(FlaskForm):
	isbn = StringField('ISBN', validators=[DataRequired(), isbn_validator])
	submit = SubmitField('Look Up')

class BookEditForm(FlaskForm):
	full_title = StringField('Full Title (required)', validators=[DataRequired(), Length(min=0, max=100)])
	pages = IntegerField('Number of Pages', validators=[NumberRange(min=0)])
	publish_date = StringField('Publish Date', validators=[date_validator])
	category = StringField('Category (required)', validators=[DataRequired(), Length(max=32)])
	authors = StringField('Authors (required)', validators=[DataRequired()])
	number_of_copies = IntegerField('Number of Copies (required)', validators=[DataRequired(), NumberRange(min=1, max=app.config['MAX_NUMBER_OF_COPIES'])])
	submit = SubmitField('Edit')

class BookEntryForm(FlaskForm):
	isbn_10 = StringField('ISBN-10', validators=[Optional(), Length(min=10, max=10)])
	isbn_13 = StringField('ISBN-13', validators=[Optional(), Length(min=13, max=13)])
	full_title = StringField('Full Title (required)', validators=[DataRequired(), Length(min=0, max=100)])
	pages = IntegerField('Number of Pages', validators=[NumberRange(min=0)])
	publish_date = StringField('Publish Date', validators=[date_validator])
	category = StringField('Category', validators=[Optional(), Length(max=32)])
	authors = StringField('Authors (required)', validators=[DataRequired()])
	cover = HiddenField('Cover')
	number_of_copies = IntegerField('Number of Copies (required)', validators=[DataRequired(), NumberRange(min=1, max=app.config['MAX_NUMBER_OF_COPIES'])])
	submit = SubmitField('Add to Collection')

class LoanPhoneForm(FlaskForm):
	search_type = SelectField('Search Type', choices=['Name', 'Phone'], default='Name')
	q = StringField('Contact Name', validators=[DataRequired(), Length(max=32)])
	submit = SubmitField('Look Up')

class LoanBookForm(FlaskForm):
	phone_num = StringField('Contact Phone', validators=[Optional(), Length(min=14, max=14)])
	name = StringField('Contact Name (required)', validators=[DataRequired(), Length(max=32)])
	loan_duration_length = IntegerField('Length', validators=[DataRequired(), NumberRange(min=1, max=30)])
	loan_duration_unit = SelectField('Unit', choices=[('days', 'Days'), ('weeks', 'Weeks')], default='weeks', validators=[DataRequired()])
	submit = SubmitField('Check Out')

class LoanExtendForm(FlaskForm):
	loan_duration_length = IntegerField('Loan Duration Length', validators=[DataRequired(), NumberRange(min=1, max=30)])
	loan_duration_unit = SelectField('Loan Duration Unit', choices=[('days', 'Days'), ('weeks', 'Weeks')], default='weeks', validators=[DataRequired()])
	submit = SubmitField('Extend')

class EmptyForm(FlaskForm):
	submit = SubmitField('Submit')
			


