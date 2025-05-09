from astropy.time import Time

from stdatamodels import DataModel as _DataModel


__all__ = ["JwstDataModel"]


class JwstDataModel(_DataModel):
    """Base class for JWST data models."""

    schema_url = "http://stsci.edu/schemas/jwst_datamodel/core.schema"

    @property
    def crds_observatory(self):
        """
        Get CRDS observatory code for this model.

        Returns
        -------
        str
            The observatory code.
        """
        return "jwst"

    def get_crds_parameters(self):
        """
        Get parameters used by CRDS to select references for this model.

        Returns
        -------
        dict
            Dictionary of CRDS parameters
        """
        return {
            key: val
            for key, val in self.to_flat_dict(include_arrays=False).items()
            if isinstance(val, (str, int, float, complex, bool))
        }

    def on_init(self, init):
        """
        Run a hook before returning a newly created model instance.

        Parameters
        ----------
        init : object
            First argument to __init__.
        """
        super().on_init(init)

        if not self.meta.hasattr("date"):
            self.meta.date = Time.now().isot

    def on_save(self, init):
        """
        Run a hook before writing a model to a file (FITS or ASDF).

        Parameters
        ----------
        init : str
            The path to the file that we're about to save to.
        """
        super().on_save(init)

        self.meta.date = Time.now().isot
