import base64
from bs4 import BeautifulSoup
import datetime
import json
from os.path import isfile, getsize
import re
import requests
import six
import sqlite3
import urllib
import webbrowser

from constants import *


def authenticate_client():
    """ Spotify's Auth Flow, a three steps process """
    auth_code = get_auth_code()
    return get_access_token(auth_code)


def get_auth_code():
    """ 1st Step of Auth Process """
    if isfile('auth.txt') and getsize('auth.txt') > 0:
        with open('auth.txt', 'r') as f:
            auth_code = f.read()
    else:
        auth_code = fetch_auth_code()
        save_auth_code(auth_code)

    return auth_code


def get_access_token(auth_code):
    """ 2nd Step of Auth Process """
    tokens = cursor.execute('SELECT access_token, refresh_token, add_time '
                            'from tokens ORDER BY add_time DESC')

    token_row = tokens.fetchone()
    if token_row is not None:
        access_token, refresh_token, add_time = token_row[0], token_row[1], token_row[2]
        if not check_token_validity(add_time):
            return fetch_refreshed_token(refresh_token)
        else:
            return access_token
    else:
        access_token, refresh_token, add_time = fetch_access_token(auth_code)
        return access_token


def fetch_access_token(auth_code):
    """ Fetches Access Token from Spotify API """
    payload = {'grant_type': 'authorization_code',
               'code': str(auth_code), 'redirect_uri': REDIRECT_URI}
    auth_headers = base64.b64encode(six.text_type(CLIENT_ID + ':' + CLIENT_SECRET).encode('ascii'))
    headers = {'Authorization': 'Basic ' + auth_headers.decode('ascii')}

    response = requests.post(OAUTH_TOKENS_URL, headers=headers, data=payload)
    response_data = json.loads(response.content)

    cursor.execute('INSERT INTO tokens('
                   'access_token, '
                   'token_type, scope, '
                   'expires_in, '
                   'refresh_token, '
                   'add_time) '
                   'VALUES (?,?,?,?,?,?)',
                   (response_data['access_token'],
                    response_data['token_type'],
                    response_data['scope'],
                    response_data['expires_in'],
                    response_data['refresh_token'],
                    datetime.datetime.now()))

    tokens = cursor.execute('SELECT access_token, refresh_token, add_time '
                            'from tokens ORDER BY add_time DESC')

    access_token, refresh_token, add_time = tokens.fetchone()
    return access_token, refresh_token, add_time


def fetch_refreshed_token(refresh_token):
    """ Fetches a new access token using refresh token """
    payload = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
    auth_headers = base64.b64encode(six.text_type(CLIENT_ID + ':' + CLIENT_SECRET).encode('ascii'))
    headers = {'Authorization': 'Basic ' + auth_headers.decode('ascii')}

    response = requests.post(OAUTH_TOKENS_URL, data=payload, headers=headers)
    response_data = json.loads(response.content)

    cursor.execute('INSERT INTO tokens('
                   'access_token, '
                   'token_type, scope, '
                   'expires_in, '
                   'refresh_token, '
                   'add_time) '
                   'VALUES (?,?,?,?,?,?)',
                   (response_data['access_token'],
                    response_data['token_type'],
                    response_data['scope'],
                    response_data['expires_in'],
                    refresh_token,
                    datetime.datetime.now()))

    return response_data['access_token']


def fetch_auth_code():
    """ Fetches Auth code by making a request to Spotify OAUTH URL """
    data = {'client_id': CLIENT_ID, 'response_type': 'code', 'redirect_uri': REDIRECT_URI,
            'scope': 'playlist-modify-private playlist-modify-public'}
    payload = urllib.parse.urlencode(data)
    webbrowser.open(OAUTH_AUTHORIZE_URL + payload)
    response = prompt_user_input()
    auth_code = response.split("?code=")[1]
    return auth_code


def save_auth_code(auth_code):
    """ Saves auth code to the disk """
    with open('auth.txt', 'w') as f:
        f.write(auth_code)


def prompt_user_input():
    """ Asks the user to paste the redirect URL through any input mechanism """
    return input("Enter the redirect URL: ")


def check_token_validity(add_time):
    """ Checks if the token is older than 1 hour """
    return datetime.datetime.now() - datetime.datetime.strptime(add_time, '%Y-%m-%d %H:%M:%S.%f') < \
           datetime.timedelta(hours=1)


def fetch_user_profile(access_token):
    """ Fetches the User's Spotify Profile """
    headers = {'Authorization': 'Bearer %s' % access_token}
    response = requests.get(SPOTIFY_PROFILE_URL, headers=headers)
    response_data = json.loads(response.content)
    return response_data['id']


def fetch_playlist(access_token, name):
    """ Fetches the Playlist the user wants to be automated """
    headers = {'Authorization': 'Bearer %s' % access_token}
    response = requests.get(SPOTIFY_PLAYLIST_URL, headers=headers)
    response_data = json.loads(response.content)
    for playlist in response_data['items']:
        if playlist['name'] == name:
            return playlist['id']
        else:
            return None


def fetch_hot_songs():
    """ Scrapes a list of top songs from HotNewHipHop's Website """
    page = requests.get(url=HOT_100_URL)
    soup = BeautifulSoup(page.content, 'lxml')

    divs = soup.find_all('div', class_='chartItem-body-artist')

    song_artist_pair = []
    for div in divs:
        a = div.find('a', class_='cover-title chartItem-artist-trackTitle')
        song_name = re.sub(' +', ' ', a.text).rstrip()
        div2 = div.findChildren(
            'div', class_='chartItem-artist-info', recursive=True)
        artist_name = ""
        for element in div2:
            artist_name += element.text + " "
        artist_name = re.sub(' +', ' ', artist_name).rstrip()
        song_artist_pair.append((song_name.lower(), artist_name.lower()))

    return select_desirable_songs(song_artist_pair)


def select_desirable_songs(song_artist_pair):
    """ Creates a desirable songs list by picking relevant artists """
    cleaned_list = []
    for song, artist in song_artist_pair:
        featuring_artists = [art.split('&') for art in artist.split('\xa0feat. ')]

        for art in featuring_artists:
            for a in art:
                cleaned_list.append((song, a.rstrip()))

    return [(song, artist) for (song, artist) in cleaned_list if artist in [a.lower() for a in DESIRED_ARTISTS]]


def add_to_playlist(user_id, playlist_id, song_artist_list, access_token):
    """ Adds the songs to the playlist """
    songs_list = remove_already_added_songs(song_artist_list)
    songs_uri_list = fetch_songs_uri(songs_list, access_token)

    payload = {'uris': songs_uri_list}
    spotify_add_to_playlist_url = 'https://api.spotify.com/v1/users/{}/playlists/{}/tracks'.format(
        user_id, playlist_id)
    headers = {'Authorization': 'Bearer %s' % access_token,
               'Content-Type': 'application/json'}
    response = requests.post(spotify_add_to_playlist_url, json=payload, headers=headers)

    if response.status_code not in (400, 403, 404):
        print('The following songs have been successfully added to your playlist: \n', songs_list)
    else:
        print('Error adding songs')


def remove_already_added_songs(song_artist_list):
    """ Check's the database for the list of already added songs and removes
        the song from the list if it already exists
    """
    cursor.execute("CREATE TABLE if not exists songs(song text not null, artist text not null)")
    songs = cursor.execute("SELECT * from songs")
    songs_list = songs.fetchall()
    if len(songs_list) > 0:
        new_songs = [(song, artist) for (song, artist) in song_artist_list if (song, artist) not in songs_list]
        for song, artist in new_songs:
            cursor.execute("INSERT INTO songs(song, artist) VALUES (?,?)", (song, artist))
    else:
        for song, artist in song_artist_list:
            cursor.execute("INSERT INTO songs(song, artist) VALUES (?,?)", (song, artist))
        return song_artist_list


def fetch_songs_uri(songs_list, access_token):
    """ Returns a list of song uri's to add to the playlist.
        The list it created by searching for each track individually
    """
    song_uris = []
    for song, artist in songs_list:
        query = (song + " " + artist)
        payload = {'q': query, 'type': 'track', 'limit': 1}

        headers = {'Authorization': 'Bearer %s' % access_token}
        response = requests.get(SPOTIFY_SEARCH_URL, headers=headers, params=payload)
        response_data = response.json()
        if len(response_data['tracks']['items']) != 0:
            song_uris.append(response_data['tracks']['items'][0]['uri'])
    return song_uris


if __name__ == '__main__':
    db = sqlite3.connect('spotify.db', isolation_level=None)
    cursor = db.cursor()
    cursor.execute('CREATE TABLE if not exists tokens('
                   'access_token text not null, '
                   'token_type text not null, '
                   'scope text not null, '
                   'expires_in int not null, '
                   'refresh_token text, '
                   'add_time timestamp)'
                   )

    token = authenticate_client()
    user_id = fetch_user_profile(token)
    playlist_id = fetch_playlist(token, name='Automated Playlist')
    song_artist_list = fetch_hot_songs()
    add_to_playlist(user_id, playlist_id, song_artist_list, token)
