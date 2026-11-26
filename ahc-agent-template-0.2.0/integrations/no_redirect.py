"""Do not forward authentication to redirects. Used only by optional GitHub integration."""
import urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
