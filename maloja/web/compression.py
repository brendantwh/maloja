# gzip compression for text-like responses
# waitress and bottle don't compress anything, and the pages, scripts, stylesheets and
# the font shrink to roughly a third / half of their size

import gzip


COMPRESSIBLE_TYPES = {
	"text/html",
	"text/css",
	"text/javascript",
	"application/javascript",
	"application/json",
	"image/svg+xml",
	"font/ttf",
	"text/plain",
}
MIN_SIZE = 1024 # smaller bodies aren't worth it
LEVEL = 6


def _get_header(headers, name):
	for key, value in headers:
		if key.lower() == name:
			return value
	return None

def _without_headers(headers, *names):
	return [(k, v) for k, v in headers if k.lower() not in names]


class GzipMiddleware:

	def __init__(self, app):
		self.app = app

	def __call__(self, environ, start_response):
		# we turn ETags weak when compressing (see below), so browsers send them back as W/...
		# If-None-Match uses weak comparison anyway (RFC 9110), and bottle compares exactly,
		# so strip the prefix to keep 304 revalidation working
		if_none_match = environ.get('HTTP_IF_NONE_MATCH')
		if if_none_match and if_none_match.startswith('W/'):
			environ = dict(environ, HTTP_IF_NONE_MATCH=if_none_match[2:])

		captured = {}
		written = []

		def capture_start_response(status, headers, exc_info=None):
			captured['status'] = status
			captured['headers'] = headers
			captured['exc_info'] = exc_info
			return written.append # legacy write() callable

		body = self.app(environ, capture_start_response)

		if 'status' not in captured:
			# the app only calls start_response once iteration starts
			iterator = iter(body)
			first = next(iterator, b"")
			written.append(first)
			body_rest = iterator
		else:
			body_rest = body

		status = captured['status']
		headers = list(captured['headers'])
		exc_info = captured['exc_info']

		content_type = (_get_header(headers, 'content-type') or "").split(";")[0].strip().lower()
		compressible_type = content_type in COMPRESSIBLE_TYPES

		if compressible_type:
			# caches must keep the compressed and uncompressed variants apart
			vary = _get_header(headers, 'vary')
			if not vary:
				headers.append(('Vary', 'Accept-Encoding'))
			elif 'accept-encoding' not in vary.lower():
				headers = _without_headers(headers, 'vary') + [('Vary', vary + ', Accept-Encoding')]

		content_length = _get_header(headers, 'content-length')
		should_compress = (
			compressible_type
			and 'gzip' in environ.get('HTTP_ACCEPT_ENCODING', '').lower()
			and environ.get('REQUEST_METHOD') != 'HEAD'
			and status.startswith('200')
			and _get_header(headers, 'content-encoding') is None
			and 'no-transform' not in (_get_header(headers, 'cache-control') or '').lower()
			and (content_length is None or int(content_length) >= MIN_SIZE)
		)

		if not should_compress:
			start_response(status, headers, exc_info)
			if written:
				return _chain_and_close(written, body_rest, body)
			return body # untouched, keeps wsgi.file_wrapper optimisations

		try:
			data = b"".join(written) + b"".join(body_rest)
		finally:
			if hasattr(body, 'close'):
				body.close()

		if len(data) < MIN_SIZE:
			start_response(status, headers, exc_info)
			return [data]

		compressed = gzip.compress(data, compresslevel=LEVEL, mtime=0)
		headers = _without_headers(headers, 'content-length')
		headers.append(('Content-Encoding', 'gzip'))
		headers.append(('Content-Length', str(len(compressed))))
		etag = _get_header(headers, 'etag')
		if etag and not etag.startswith('W/'):
			# the compressed body is a different byte sequence, so a strong validator would be wrong
			headers = _without_headers(headers, 'etag') + [('ETag', 'W/' + etag)]

		start_response(status, headers, exc_info)
		return [compressed]


def _chain_and_close(first_chunks, rest, original):
	try:
		for chunk in first_chunks:
			yield chunk
		for chunk in rest:
			yield chunk
	finally:
		if hasattr(original, 'close'):
			original.close()
