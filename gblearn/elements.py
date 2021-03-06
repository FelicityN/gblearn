"""Crystal definitions and SOAP vector calculations for simple
elements.
"""
from gblearn import msg
import numpy as np
_shells = {}
"""dict: keys are element names, values are lists of shells (in Ang.).
"""

elements = {
    "Ni": ("fcc", 3.52, 28, [0]),
    "Cr": ("bcc", 2.91, 24, [0, 1]),
    "Mg": ("hcp", 3.21, 12, [0, 1])
}
"""dict: keys are element names, values are a tuple of (`str` lattice,
`float` lattice parameter, `int` element number, `list` basis indices).
"""

def atoms(element):
    """Returns a :class:`quippy.Atoms` structure for the given
    element, using the tabulated lattice parameters.

    Args:
        element (str): name of the element.
    """
    lattice = "unknown"
    if element in elements:
        import quippy.structures as structures
        lattice, latpar, Z, basis = elements[element]
        if hasattr(structures, lattice):
            return getattr(structures, lattice)(latpar, Z)

    emsg = "Element {} with structure {} is not auto-configurable."
    msg.err(emsg.format(element, lattice))
    
def shells(element, n=6, rcut=6.):
    """Returns the neighbor shells for the specified element.

    Args:
        element (str): name of the element.
        n (int): maximum number of shells to return.
        rcut (float): maximum cutoff to consider in looking for unique shells.
    """
    global _shells
    if element not in _shells:
        a = atoms(element)
        a.set_cutoff(rcut)
        a.calc_connect()
        result = []
        for i in a.indices:
            for neighb in a.connect[i]:
                dist = neighb.distance
                deltain = [abs(dist-s) < 1e-5 for s in result]
                if not any(deltain):
                    result.append(dist)

        _shells[element] = sorted(result)

    return _shells[element][0:min((n, len(_shells[element])))]

def pissnnl(element, lmax=12, nmax=12, rcut=6.0, sigma=0.5, trans_width=0.5):
    """Computes the :math:`P` matrix for the given element.

    Args:
        element (str): name of the element.
        nmax (int): bandwidth limits for the SOAP descriptor radial basis
          functions.
        lmax (int): bandwidth limits for the SOAP descriptor spherical
          harmonics.
        rcut (float): local environment finite cutoff parameter.
        sigma (float): width parameter for the Gaussians on each atom.
        trans_width (float): distance over which the coefficients in the
            radial functions are smoothly transitioned to zero.    
    """
    lattice, latpar, Z, basis = elements[element]
    from gblearn.soap import SOAPCalculator
    SC = SOAPCalculator(rcut, nmax, lmax, sigma, trans_width)
    a = atoms(element)
    return SC.calc(a, Z, basis)
