import matplotlib.pyplot as plt
import numpy as np
import torch
from torch import optim
import mgplvm as mgp
from sklearn.cross_decomposition import CCA

torch.set_default_dtype(torch.float64)
device = mgp.utils.get_device()


def test_toeplitz_GP_lat_prior(use_fast_toeplitz=True):
    device = mgp.utils.get_device("cuda")  # get_device("cpu")
    d = 2  # dims of latent space
    dfit = 2  #dimensions of fitted space
    n = 50  # number of neurons
    m = 80  # number of conditions / time points
    n_z = 15  # number of inducing points
    n_samples = 10  # number of samples
    Poisson = False

    #generate from GPFA generative model
    ts = np.array([np.arange(m) for nsamp in range(n_samples)])[:, None, :]

    dts_sq = (ts[..., None] - ts[..., None, :])**2  #(n_samples x 1 x m x m)
    dts_sq = np.sum(dts_sq, axis=-3)  #(n_samples x m x m)
    K = np.exp(-dts_sq / (2 * 7**2)) + 1e-6 * np.eye(m)[None, ...]
    L = np.linalg.cholesky(K)
    us = np.random.normal(0, 1, size=(m, d))
    xs = L @ us  #(n_samples x m x d)
    print('xs:', xs.shape)
    w = np.random.normal(0, 1, size=(n, d))
    Y = w @ xs.transpose(0, 2, 1)  #(n_samples x n x m)
    if Poisson:
        Y = np.random.poisson(2 * (Y - np.amin(Y)))
    else:
        Y = Y + np.random.normal(0, 0.2, size=Y.shape)
    print('Y:', Y.shape, np.std(Y), np.quantile(Y, 0.99))

    data = torch.tensor(Y, device=device, dtype=torch.get_default_dtype())
    # specify manifold, kernel and rdist
    manif = mgp.manifolds.Euclid(m, dfit)
    #kernel = mgp.kernels.Linear(n, dfit)
    kernel = mgp.kernels.Linear(n,
                                dfit,
                                ard=True,
                                learn_scale=False,
                                Y=Y,
                                Poisson=Poisson)

    print("use fast toeplitz?", use_fast_toeplitz)
    lat_dist = mgp.rdist.EP_GP(manif,
                               m,
                               n_samples,
                               torch.Tensor(ts),
                               Y=Y,
                               initialization='fa',
                               use_fast_toeplitz=use_fast_toeplitz)

    ###construct prior
    lprior = mgp.lpriors.Null(manif)

    # generate model
    if Poisson:
        likelihood = mgp.likelihoods.NegativeBinomial(n, Y=Y)
        #likelihood = mgp.likelihoods.Poisson(n)
    else:
        likelihood = mgp.likelihoods.Gaussian(n, Y=Y, d=dfit)
    z = manif.inducing_points(n, n_z)
    mod = mgp.models.SvgpLvm(n, m, n_samples, z, kernel, likelihood, lat_dist,
                             lprior).to(device)

    print(mod.lat_dist.name)
    ### test that training runs ###
    n_mc = 16

    def cb(mod, i, loss):
        if i % 5 == 0:
            print('')
        return

    mgp.optimisers.svgp.fit(data,
                            mod,
                            optimizer=optim.Adam,
                            n_mc=n_mc,
                            max_steps=5,
                            burnin=50,
                            lrate=10e-2,
                            print_every=5,
                            stop=cb,
                            analytic_kl=True)

    try:
        print('lat ell, scale:',
              mod.lat_dist.f.ell.detach().flatten(),
              mod.lat_dist.f.scale.detach().flatten())
        print('prior:', mod.lprior.ell.detach().flatten())
    except:
        print('lat ell, scale:',
              mod.lat_dist.ell.detach().flatten(),
              mod.lat_dist.scale.detach().mean(0).mean(-1))


def test_no_toeplitz_GP_lat_prior():
    test_toeplitz_GP_lat_prior(use_fast_toeplitz=False)


def test_toeplitz_match_no_toeplitz_GP_lat_prior():
    device = mgp.utils.get_device("cuda")  # get_device("cpu")
    d = 2  # dims of latent space
    dfit = 2  #dimensions of fitted space
    n = 50  # number of neurons
    m = 80  # number of conditions / time points
    n_z = 15  # number of inducing points
    n_samples = 10  # number of samples
    Poisson = False

    #generate from GPFA generative model
    ts = np.array([np.arange(m) for nsamp in range(n_samples)])[:, None, :]

    dts_sq = (ts[..., None] - ts[..., None, :])**2  #(n_samples x 1 x m x m)
    dts_sq = np.sum(dts_sq, axis=-3)  #(n_samples x m x m)
    K = np.exp(-dts_sq / (2 * 7**2)) + 1e-6 * np.eye(m)[None, ...]
    L = np.linalg.cholesky(K)
    us = np.random.normal(0, 1, size=(m, d))
    xs = L @ us  #(n_samples x m x d)
    print('xs:', xs.shape)
    w = np.random.normal(0, 1, size=(n, d))
    Y = w @ xs.transpose(0, 2, 1)  #(n_samples x n x m)
    if Poisson:
        Y = np.random.poisson(2 * (Y - np.amin(Y)))
    else:
        Y = Y + np.random.normal(0, 0.2, size=Y.shape)
    print('Y:', Y.shape, np.std(Y), np.quantile(Y, 0.99))

    data = torch.tensor(Y, device=device, dtype=torch.get_default_dtype())
    # specify manifold, kernel and rdist
    manif = mgp.manifolds.Euclid(m, dfit)
    #kernel = mgp.kernels.Linear(n, dfit)
    torch.manual_seed(0)
    lat_dist = mgp.rdist.EP_GP(manif,
                               m,
                               n_samples,
                               torch.Tensor(ts),
                               Y=Y,
                               initialization='fa',
                               use_fast_toeplitz=False)
    sample1, sample2 = lat_dist.sample((2,))
    prms1, prms2 = lat_dist.prms

    torch.manual_seed(0)
    lat_dist_toeplitz = mgp.rdist.EP_GP(manif,
                                        m,
                                        n_samples,
                                        torch.Tensor(ts),
                                        Y=Y,
                                        initialization='fa',
                                        use_fast_toeplitz=True)

    sample_toeplitz1, sample_toeplitz2 = lat_dist_toeplitz.sample((2,))
    prms_toeplitz1, prms_toeplitz2 = lat_dist_toeplitz.prms

    assert torch.allclose(sample1, sample_toeplitz1)
    assert torch.allclose(prms1, prms_toeplitz1)
    assert torch.allclose(sample2, sample_toeplitz2)
    assert torch.allclose(prms2, sym_toeplitz(prms_toeplitz2))


if __name__ == '__main__':
    test_toeplitz_GP_lat_prior()
    test_no_toeplitz_GP_lat_prior()
    test_toeplitz_match_no_toeplitz_GP_lat_prior()
