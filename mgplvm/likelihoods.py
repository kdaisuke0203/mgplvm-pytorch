import torch
from torch import Tensor
import torch.nn as nn
import abc
from .base import Module
from typing import Optional
import torch.distributions as dists
import numpy as np

log2pi: float = np.log(2 * np.pi)
n_gh_locs: int = 20  # default number of Gauss-Hermite points


class Likelihoods(Module, metaclass=abc.ABCMeta):
    def __init__(self, n: int, m: int, n_gh_locs: int):
        super().__init__()
        self.n = n
        self.m = m
        self.n_gh_locs = n_gh_locs

    @abc.abstractproperty
    def log_prob(y):
        pass

    @abc.abstractproperty
    def variational_expectation(y, mu, var):
        pass


class Gaussian(Likelihoods):
    def __init__(self,
                 n: int,
                 m: int,
                 variance: Optional[Tensor] = None,
                 n_gh_locs=n_gh_locs):
        super().__init__(n, m, n_gh_locs)
        sigma = 1 * torch.ones(n, ) if variance is None else torch.sqrt(
            torch.tensor(variance, dtype=torch.get_default_dtype()))
        self.sigma = nn.Parameter(data=sigma, requires_grad=True)

    @property
    def prms(self):
        variance = torch.square(self.sigma)
        return variance

    def log_prob(self, y):
        pass

    def variational_expectation(self, n_samples, y, fmu, fvar):
        n_b = fmu.shape[0]
        variance = self.prms
        ve1 = -0.5 * log2pi * self.m * self.n * n_samples * n_b
        ve2 = -0.5 * torch.log(variance).sum() * n_samples * n_b * self.m
        ve3 = -0.5 * torch.square(y - fmu) / variance[..., None, None]
        ve4 = -0.5 * fvar / variance[..., None] * n_samples
        return ve1.sum() + ve2.sum() + ve3.sum() + ve4.sum()


class Poisson(Likelihoods):
    def __init__(self,
                 n: int,
                 m: int,
                 inv_link=torch.exp,
                 binsize=1,
                 c: Optional[Tensor] = None,
                 d: Optional[Tensor] = None,
                 fixed_c=False,
                 fixed_d=False,
                 n_gh_locs: Optional[int] = n_gh_locs):
        super().__init__(n, m, n_gh_locs)
        self.inv_link = inv_link
        self.binsize = binsize
        c = torch.ones(n, ) if c is None else c
        d = torch.zeros(n, ) if d is None else d
        self.c = nn.Parameter(data=c, requires_grad=not fixed_c)
        self.d = nn.Parameter(data=d, requires_grad=not fixed_d)

    @property
    def prms(self):
        return self.c, self.d

    def log_prob(self, lamb, y):
        p = dists.Poisson(lamb)
        return p.log_prob(y)

    def variational_expectation(self, n_samples, y, fmu, fvar, gh=False):
        c, d = self.prms
        fmu = c[..., None, None] * fmu + d[..., None, None]
        fvar = fvar * torch.square(c[..., None])
        if self.inv_link == torch.exp and not gh:
            n_b = fmu.shape[0]
            v1 = (y * fmu) - (self.binsize *
                              torch.exp(fmu + 0.5 * fvar[..., None]))
            v2 = (y * np.log(self.binsize) - torch.lgamma(y + 1)) * n_b
            return v1.sum() + v2.sum()
        else:
            # use Gauss-Hermite quadrature to approximate integral
            locs, ws = np.polynomial.hermite.hermgauss(self.n_gh_locs)
            ws = torch.Tensor(ws).to(fmu.device)
            locs = torch.Tensor(locs).to(fvar.device)
            fvar = fvar[..., None]
            locs = self.inv_link(torch.sqrt(2. * fvar) * locs +
                                 fmu) * self.binsize
            lp = self.log_prob(locs, y)
            return torch.sum(1 / np.sqrt(np.pi) * lp * ws)


class NegativeBinomial(Likelihoods):
    def __init__(self,
                 n: int,
                 m: int,
                 inv_link=lambda x: x,
                 binsize=1,
                 total_count: Optional[Tensor] = None,
                 c: Optional[Tensor] = None,
                 d: Optional[Tensor] = None,
                 fixed_total_count=False,
                 fixed_c=False,
                 fixed_d=False,
                 n_gh_locs: Optional[int] = n_gh_locs):
        super().__init__(n, m, n_gh_locs)
        self.inv_link = inv_link
        self.binsize = binsize
        total_count = 2 * torch.ones(
            n, ) if total_count is None else total_count
        total_count = dists.transform_to(
            dists.constraints.greater_than_eq(0)).inv(total_count)
        c = torch.ones(n, ) if c is None else c
        d = torch.zeros(n, ) if d is None else d
        self.total_count = nn.Parameter(data=total_count,
                                        requires_grad=not fixed_total_count)
        self.c = nn.Parameter(data=c, requires_grad=not fixed_c)
        self.d = nn.Parameter(data=d, requires_grad=not fixed_d)

    @property
    def prms(self):
        total_count = dists.transform_to(dists.constraints.greater_than_eq(0))(
            self.total_count)
        return total_count, self.c, self.d

    def log_prob(self, total_count, rate, y):
        p = dists.NegativeBinomial(total_count[..., None, None], logits=rate)
        return p.log_prob(y)

    def variational_expectation(self, n_samples, y, fmu, fvar, gh=False):
        total_count, c, d = self.prms
        fmu = c[..., None, None] * fmu + d[..., None, None]
        fvar = fvar * torch.square(c[..., None])
        # use Gauss-Hermite quadrature to approximate integral
        locs, ws = np.polynomial.hermite.hermgauss(self.n_gh_locs)
        ws = torch.Tensor(ws).to(fmu.device)
        locs = torch.Tensor(locs).to(fvar.device)
        fvar = fvar[..., None]
        locs = self.inv_link(torch.sqrt(2. * fvar) * locs + fmu) * self.binsize
        lp = self.log_prob(total_count, locs, y)
        return torch.sum(1 / np.sqrt(np.pi) * lp * ws)