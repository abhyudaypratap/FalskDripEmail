# -*- coding: utf-8 -*-

import os
import flask
import requests
import json

import uuid


import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery

from werkzeug.contrib.fixers import ProxyFix

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_ckeditor import CKEditor

from .models import User, Campaign, Email, db
from .forms import DripEmailBasicDetailsForm, DripEmailTemplateForm

from .utils.emailUtils import CreateMessage, send_message

from flask import request, render_template, flash

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

app = flask.Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("SQLALCHEMY_DATABASE_URI")
# app.config['SQLALCHEMY_DATABASE_URI'] = 'postgres://lmeorjjdferkzy:a3d0f53608d39f2e334cd3b8e1aa3c4340a9ed6222f3ef490968c7378e570cac@ec2-54-225-94-143.compute-1.amazonaws.com:5432/da7shc2afvnmcq'
app.wsgi_app = ProxyFix(app.wsgi_app)
with app.app_context():
    db.init_app(app)
migrate = Migrate(app, db)
ckeditor = CKEditor(app)

# uncomment below function to interact with DB using db.<command>
# not-recommended use flask-migrate for DB migrations
# def create_app():
#     app = flask.Flask(__name__)
#     db.init_app(app)
#     return app


# This variable specifies the name of a file that contains the OAuth 2.0
# information for this application, including its client_id and client_secret.
CLIENT_SECRETS_FILE = os.environ.get("CLIENT_SECRETS_FILE")

# This OAuth 2.0 access scope allows for full read/write access to the
# authenticated user's account and requires requests to use an SSL connection.
SCOPES = ['https://mail.google.com']
API_SERVICE_NAME = 'gmail'
API_VERSION = 'v1'


@app.route('/')
def index():
  return ('<a href="/new-campaign">Start Drip Campaign</a>')


@app.route('/new-campaign', methods=['GET', 'POST'])
def test_api_request():
    if 'credentials' not in flask.session:
        return flask.redirect('authorize')

    # Load credentials from the session.
    credentials = google.oauth2.credentials.Credentials(
      **flask.session['credentials'])

    gmail = googleapiclient.discovery.build(
      API_SERVICE_NAME, API_VERSION, credentials=credentials)
    flask.session['credentials'] = credentials_to_dict(credentials)

    # get the user profile using Gmail API
    userProfile = gmail.users().getProfile(userId = "me").execute()
    # create a User object with the data provided by Gmail API
    user =  User.query.filter_by(email=userProfile['emailAddress']).first()
    flask.session['fromEmail'] = userProfile['emailAddress']

    # add user to DB if not present already
    if not user:
        user = User(email = userProfile['emailAddress'], unique_id = uuid.uuid4().hex)
        db.session.add(user)
        db.session.commit()

    form = DripEmailBasicDetailsForm(request.form)
    if request.method == 'POST' and form.validate():
        # create a Campaign object based on the form data.
        campaign = Campaign(title = form.title.data, stages = form.noOfStages.data,
            frequency = 2, recipients = form.receiverList.data,
            user_id = user.unique_id, unique_id = uuid.uuid4().hex)

        # store some session variables to be used in emailTemplate form
        flask.session['campaign_id'] = campaign.unique_id
        flask.session['campaign'] = campaign.title
        flask.session['frequency'] = campaign.stages

        # add campaign to DB
        db.session.add(campaign)
        db.session.commit()
        flash('Thanks for registering')
        return flask.redirect(flask.url_for('addEmail'))
    return render_template('register.html', form=form)

@app.route('/authorize')
def authorize():
  # Create flow instance to manage the OAuth 2.0 Authorization Grant Flow steps.
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
      CLIENT_SECRETS_FILE, scopes=SCOPES)

    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
      # Enable offline access so that you can refresh an access token without
      # re-prompting the user for permission. Recommended for web server apps.
      access_type='offline',
      # Enable incremental authorization. Recommended as a best practice.
      include_granted_scopes='true',
      prompt = 'consent')

    # Store the state so the callback can verify the auth server response.
    flask.session['state'] = state

    return flask.redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Specify the state when creating the flow in the callback so that it can
    # verified in the authorization server response.
    state = flask.session['state']

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
      CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = flask.url_for('oauth2callback', _external=True)

    # Use the authorization server's response to fetch the OAuth 2.0 tokens.
    authorization_response = flask.request.url
    flow.fetch_token(authorization_response=authorization_response)

    # Store credentials in the session.
    # ACTION ITEM: In a production app, you likely want to save these
    #              credentials in a persistent database instead.
    credentials = flow.credentials
    flask.session['credentials'] = credentials_to_dict(credentials)

    return flask.redirect(flask.url_for('test_api_request'))

@app.route('/addEmail', methods=['GET', 'POST'])
def addEmail():
    if 'credentials' not in flask.session:
        return flask.redirect('authorize')

    # Load credentials from the session.
    credentials = google.oauth2.credentials.Credentials(
      **flask.session['credentials'])

    # get the gmail service for interacting with Gmail API.
    gmail = googleapiclient.discovery.build(
      API_SERVICE_NAME, API_VERSION, credentials=credentials)

    # save credentials back to session
    # ideally use DB for this purpose
    flask.session['credentials'] = credentials_to_dict(credentials)

    # use DripEmailTemplateForm
    form = DripEmailTemplateForm(request.form)

    if request.method == 'POST' and form.validate():
        # create Email object with the details populated in DripEmailTemplateForm
        email = Email(subject = form.subject.data, unique_id = uuid.uuid4().hex,
                body = form.emailTemplate.data,
                campaign_id = flask.session['campaign_id'])
        # Add Email to DB
        db.session.add(email)
        db.session.commit()
        recipients = Campaign.query.filter_by(
                        unique_id=flask.session['campaign_id']).first().recipients

        # counter for number of email templates required
        flask.session['frequency'] = flask.session['frequency'] - 1
        if flask.session['frequency'] == 0:
            # see documentation in utils.emailUtils.CreateMessage
            # use celery background task here to send emails
            for recipient in recipients.split(","):
                message =  CreateMessage(recipient,
                        flask.session['fromEmail'],
                         "Welcome Message " + flask.session['campaign'],
                         "Welcome to Drip Campaign " + flask.session['campaign'])
                message['raw'] = message['raw'].decode('utf-8')
                # see documentation in utils.emailUtils.send_message
                send_message(gmail, "me", message)
            # return campaign started after all email templates are filled
            return ("<p>Campaign Started</p> "
                    + '<p><a href="/new-campaign">Add Another Campaign</a></p>')

        else:
            # return DripEmailTemplateForm for adding more email templates
             return render_template('email.html', form = form)
    return render_template('email.html', form = form)

def credentials_to_dict(credentials):
    return {'token': credentials.token,
          'refresh_token': credentials.refresh_token,
          'token_uri': credentials.token_uri,
          'client_id': credentials.client_id,
          'client_secret': credentials.client_secret,
          'scopes': credentials.scopes}


if __name__ == '__main__':
    # When running locally, disable OAuthlib's HTTPs verification.
    # ACTION ITEM for developers:
    #     When running in production *do not* leave this option enabled.
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    # Specify a hostname and port that are set as a valid redirect URI
    # for your API project in the Google API Console.
    app.run('localhost', 8080, debug=True)
