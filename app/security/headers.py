"""Content security policies for website pages."""

CONSOLE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
               "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ENQUIRY_CSP = CONSOLE_CSP.replace("form-action 'none'", "form-action 'self'")
