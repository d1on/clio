import datetime
import logging
import os

from django.utils import simplejson
from google.appengine.api import channel
from google.appengine.api import prospective_search
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app

from clio.config import config
from clio import model


class IndexHandler(webapp.RequestHandler):
  """Serve up the Clio admin interface."""

  def get(self):
    client_id = os.urandom(16).encode('hex')
    channel_key = channel.create_channel(client_id)
    template_path = os.path.join(os.path.dirname(__file__),
                                 'templates', 'index.html')
    self.response.out.write(template.render(template_path, {
        'config': config,
        'client_id': client_id,
        'channel_key': channel_key,
    }))


class SubscribeHandler(webapp.RequestHandler):
  """Handle subscription requests from clients."""

  def post(self):
    sub = model.Subscription(
        client_id=self.request.POST['client_id'])
    sub.put()
    prospective_search.subscribe(
        model.RequestRecord,
        self.request.POST['query'],
        str(sub.key()),
        lease_duration_sec=config.SUBSCRIPTION_TIMEOUT.seconds)
    self.response.out.write(str(sub.key()))


class MatchHandler(webapp.RequestHandler):
  """Process matching log entries and send them to clients."""

  def post(self):
    # Fetch the log record
    record = prospective_search.get_document(self.request)
    record_data = record.to_json()

    # Fetch the set of subscribers to send this record to
    subscriber_keys = map(db.Key, self.request.get_all('id'))
    subscribers = db.get(subscriber_keys)

    for subscriber_key, subscriber in zip(subscriber_keys, subscribers):
      # If the subscription has been deleted from the datastore, delete it
      # from the matcher API.
      if not subscriber:
        logging.error("Subscription %s deleted!", subscriber_key)
        prospective_search.unsubscribe(model.RequestRecord, subscriber_key)
      else:
        data = simplejson.dumps({
            'subscription_key': str(subscriber_key),
            'data': record_data,
        })
        channel.send_message(subscriber.client_id, data)

def handle_disconnection(client_id):
  """Handles a channel disconnection for a Clio channel."""
  # Find all their subscriptions and delete them.
  q = model.Subscription.all().filter('client_id =', client_id)
  subscriptions = q.fetch(1000)
  for sub in subscriptions:
    prospective_search.unsubscribe(model.RequestRecord, str(sub.key()))
  db.delete(subscriptions)


class ChannelConnectHandler(webapp.RequestHandler):
  def post(self):
    pass


class ChannelDisconnectHandler(webapp.RequestHandler):
  def post(self):
    handle_disconnection(self.request.get('from'))


application = webapp.WSGIApplication([
    (config.BASE_URL + '/', IndexHandler),
    (config.BASE_URL + '/subscribe', SubscribeHandler),
    (config.QUEUE_URL, MatchHandler),
    ('/_ah/channel/connected/', ChannelConnectHandler),
    ('/_ah/channel/disconnected/', ChannelDisconnectHandler),
])


def main():
  run_wsgi_app(application)


if __name__ == "__main__":
  main()
