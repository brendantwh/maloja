import io
import os
from wsgiref.util import setup_testing_defaults

from PIL import Image

from maloja.pkg_global.conf import data_dir


def get(app, path):
	environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "wsgi.input": io.BytesIO()}
	setup_testing_defaults(environ)
	result = {}

	def start_response(status, headers, exc_info=None):
		result["status"] = int(status.split()[0])
		result["headers"] = dict(headers)

	b"".join(app(environ, start_response))
	return result["status"], result["headers"]


def test_cached_image_is_served_with_its_type():
	from maloja.server import webserver

	# cached images are stored without a file extension
	os.makedirs(data_dir['cache']('images'), exist_ok=True)
	Image.new("RGB", (4, 4)).save(data_dir['cache']('images', 'cached-image'), format="PNG")

	status, headers = get(webserver, "/cacheimages/cached-image")
	assert status == 200
	assert headers["Content-Type"] == "image/png"
