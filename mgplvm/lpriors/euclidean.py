import numpy as np
import torch
import torch.nn as nn
import torch.distributions as dists
from ..kernels import Kernel
from ..manifolds import Euclid
from ..manifolds.base import Manifold
from ..models import Svgp
from ..inducing_variables import InducingPoints
from ..likelihoods import Gaussian
from .common import Lprior
from typing import Optional


class LpriorEuclid(Lprior):
    def __init__(self, manif):
        if not isinstance(manif, Euclid):
            raise Exception("GP prior only works with Euclidean manifolds")

        super().__init__(manif)


class GP(LpriorEuclid):
    name = "GP"

    def __init__(self,
                 manif: Manifold,
                 kernel: Kernel,
                 ts: torch.Tensor,
                 n_z: Optional[int] = 20,
                 tmax: Optional[int] = 1):
        '''
        instantiate with a Kernel. This already specifies which parameters are learnable
        specifying tmax helps initialize the inducing points
        '''
        super().__init__(manif)
        d = manif.d
        assert (tmax is not None)
        assert (n_z is not None)
        #1d latent and n_z inducing points
        zinit = torch.linspace(0., tmax, n_z).reshape(1, 1, n_z)
        #separate inducing points for each latent dimension
        z = InducingPoints(d, 1, n_z, z=zinit.repeat(d, 1, 1))
        self.ts = ts
        lik = Gaussian(d, variance=np.square(0.2), learn_sigma=False
                       )  #consider fixing this to a small value as in GPFA
        self.svgp = Svgp(kernel, d, z, lik, whiten=True)  #construct svgp

    @property
    def prms(self):
        q_mu, q_sqrt, z = self.svgp.prms
        sigma_n = self.svgp.likelihood.prms
        return q_mu, q_sqrt, z, sigma_n

    def forward(self, x, batch_idxs=None):
        '''
        x is a latent of shape (n_mc x n_samples x mx x d)
        ts is the corresponding timepoints of shape (n_samples x mx)
        '''
        m = self.ts.shape[0]
        batch_size = m if batch_idxs is None else len(batch_idxs)
        ts = self.ts if batch_idxs is None else self.ts[batch_idxs]
        ts = ts.to(x.device)
        n_mc, n_samples, T, d = x.shape
        # x now has shape (n_samples, n_mc*d, T)
        x = x.permute(1, 0, 3, 2).reshape(n_samples, n_mc * d, T)

        # (1, n_mc . d) (n_mc . d)
        svgp_lik, svgp_kl = self.svgp.elbo(1, x,
                                           ts.reshape(1, n_samples, 1, -1))
        # Here, we need to rescale the KL term so that it is per batch
        # as the inducing points are shared across the full batch
        elbo = svgp_lik - ((batch_size / m) * svgp_kl)
        return elbo.reshape(n_mc, d).sum(-1)  #sum over dimensions

    @property
    def msg(self):
        ell = self.svgp.kernel.prms[1].mean()
        noise = self.svgp.likelihood.prms.sqrt()

        return (' prior ell {:.3f} | prior noise {:.3f} |').format(
            ell.item(), noise.item())
