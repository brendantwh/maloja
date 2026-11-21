from ._apikeys import apikeystore


from bottle import response



def init_apis(server):

	from . import native_v1
	from .audioscrobbler import Audioscrobbler
	from .audioscrobbler_legacy import AudioscrobblerLegacy
	from .listenbrainz import Listenbrainz

	native_apis = [
		native_v1.api
	]
	standardized_apis = [
		Listenbrainz(),
		Audioscrobbler(),
		AudioscrobblerLegacy()
	]

	for api in native_apis:
		api.mount(server=server,path="apis/"+api.__apipath__)

	for api in standardized_apis:
		# serve every alias directly: a redirect would turn POST into GET and lose the scrobble,
		# and many clients don't follow redirects at all
		for alias in api.__aliases__:
			api.nimrodelapi.mount(server=server,path="apis/" + alias)
		# each mount registers the api again, but the explorer should list it once
		server._apis[:] = list(dict.fromkeys(server._apis))

	def invalid_api(pth=''):
		response.status = 404
		return {"error":"Invalid API"}

	server.get("/apis/<pth:path>")(invalid_api)
	server.post("/apis/<pth:path>")(invalid_api)

	server.get("/apis")(invalid_api)
	server.post("/apis")(invalid_api)
