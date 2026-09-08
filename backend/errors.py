"""Erreurs domaine Palier 4 : disparition explicite d'une ressource.

Une ressource perdue ne doit jamais devenir un redémarrage silencieux.
Chaque échec porte : resource, code stable, message public sûr, retryable.
Aucun secret, aucune URL credentialée, aucune stack trace ne transite ici.
"""


class ResourceUnavailable(Exception):
    """Une ressource nécessaire au run a disparu ou est inutilisable."""

    def __init__(
        self,
        resource: str,
        code: str,
        public_message: str,
        retryable: bool = False,
    ):
        super().__init__(public_message)
        self.resource = resource
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class RunStopped(Exception):
    """Le run a été interrompu par le kill switch opérateur."""

    def __init__(self, run_id: str):
        super().__init__("run arrêté par l'opérateur")
        self.run_id = run_id
