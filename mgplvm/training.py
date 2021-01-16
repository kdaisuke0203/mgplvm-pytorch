from __future__ import print_function
import numpy as np
import mgplvm
from mgplvm.utils import softplus
from mgplvm.manifolds import Torus, Euclid, So3
import torch
from torch import optim, Tensor
from torch.optim.lr_scheduler import LambdaLR
from typing import List


def sgp(Y,
        model,
        device,
        optimizer=optim.Adam,
        outdir="results/vanilla",
        max_steps=1000,
        n_b=128,
        burnin='default',
        lrate=1e-3,
        print_every=10,
        callback=None,
        trainGP=True,
        nbatch=1,
        Tfix=slice(0),
        sigma_thresh=0.0001):
    def _Tlearn_hook(grad):
        ''' used to 'mask' some gradients for cv'''
        grad[Tfix, ...] *= 0
        return grad

    if burnin == 'default':
        burnin = 5 / lrate
    n, m, _ = Y.shape  # neurons, conditions, samples
    data = torch.tensor(Y, dtype=torch.get_default_dtype()).to(device)

    # parameters to be optimized
    #if type(model) == mgplvm.models.SgpComb:
    #    params = sort_params_prod(model, _Tlearn_hook, trainGP)
    params = sort_params(model, _Tlearn_hook, trainGP)
    optimizer = optimizer(params[0], lr=lrate)  # instantiate optimizer
    optimizer.add_param_group({'params': params[1]})

    # set learning rate schedule so sigma updates have a burn-in period
    def fburn(x):
        return 1 - np.exp(-x / (3 * burnin))

    LRfuncs = [lambda x: 1, fburn]
    scheduler = LambdaLR(optimizer, lr_lambda=LRfuncs)

    if nbatch > 1:
        n_b = int(round(n_b / nbatch))
    i_step = -1
    Ls = np.zeros(10)
    while (i_step < max_steps) and (np.std(Ls) > sigma_thresh or i_step <
                                    (2.5 * burnin)):
        i_step += 1
        optimizer.zero_grad()
        ramp = 1 - np.exp(-i_step / burnin)  # ramp the entropy

        for _ in range(nbatch):
            sgp_elbo, kl = model(data, n_b)  # log p(Y|G), KL(Q(G), p(G))
            loss = (-sgp_elbo + (ramp * kl))  # -LL
            loss.backward()

        optimizer.step()
        scheduler.step()
        if i_step % print_every == 0:
            print_sgp_progress(model, i_step, n, m, sgp_elbo, kl, loss)
        if callback is not None:
            callback(model, i_step)

        Ls[i_step % 10] = loss.item() / (n * m)

    return model


def print_sgp_progress(model, i, n, m, sgp_elbo, kl, loss):
    if type(model.manif) == So3:
        mu_mag = np.mean(
            np.sqrt(
                np.sum(model.manif.prms.data.cpu().numpy()[:, 1:]**2, axis=1)))
    elif type(model.manif) == list:
        ms = [m.prms.data.cpu().numpy() for m in model.manif]
        mu_mag = np.mean([np.mean(np.sqrt(np.sum(m**2, axis=1))) for m in ms])
    else:
        mu_mag = np.mean(
            np.sqrt(np.sum(model.manif.prms.data.cpu().numpy()**2, axis=1)))

    #if type(model) == mgplvm.models.SgpComb:
    #    sigs = [r.prms.data.cpu().numpy() for r in model.rdist]
    #    sigs = [np.concatenate([np.diag(s) for s in sig]) for sig in sigs]
    #    sig = np.median(np.concatenate(sigs))
    #    alpha_mag = torch.stack([p[0] for p in model.kernel.prms
    #                             ]).mean().data.cpu().numpy()
    #    ell_mag = torch.stack([p[1] for p in model.kernel.prms
    #                           ]).mean().data.cpu().numpy()
    sig = np.median(
        np.concatenate(
            [np.diag(sig) for sig in model.rdist.prms.data.cpu().numpy()]))
    alpha_mag, ell_mag = [
        val.mean().data.cpu().numpy() for val in model.kernel.prms
    ]

    msg = (
        '\riter {:4d} | elbo {:.4f} | kl {:.4f} | loss {:.4f} | |mu| {:.4f} | alpha_sqr {:.4f} | ell {:.4f}'
    ).format(i,
             sgp_elbo.item() / (n * m),
             kl.item() / (n * m),
             loss.item() / (n * m), mu_mag, alpha_mag**2, ell_mag)
    print(msg + " | " + model.lprior.msg, "\r")


def sort_params(model, hook, trainGP, svgp=False):
    '''apply burnin period to Sigma_Q and alpha^2
    allow for masking of certain conditions for use in crossvalidation'''

    # parameters to be optimized

    params: List[List[Tensor]] = [[], [], []] if svgp else [[], []]

    for param in model.parameters():
        if (param.shape == model.lat_dist.gamma.shape) and torch.all(
                param == model.lat_dist.gamma):
            param.register_hook(hook)  # option to mask gradients
            params[1].append(param)
        elif (param.shape == model.lat_dist.manif.mu.shape) and torch.all(
                param == model.lat_dist.manif.mu):
            param.register_hook(hook)  # option to mask gradients
            params[0].append(param)
        elif svgp and (param.shape == model.svgp.q_mu.shape) and torch.all(
                param == model.svgp.q_mu):
            params[2].append(param)
        elif svgp and (param.shape == model.svgp.q_sqrt.shape) and torch.all(
                param == model.svgp.q_sqrt):
            params[2].append(param)
        elif trainGP:  # only update GP parameters if trainGP
            # add ell to group 2
            if (('QuadExp' in model.kernel.name)
                    and (param.shape == model.kernel.ell.shape)
                    and torch.all(param == model.kernel.ell)):
                params[1].append(param)
            else:
                params[0].append(param)

    return params


def sort_params_prod(model, hook, trainGP):
    '''apply burnin period to Sigma_Q and alpha^2
    allow for masking of certain conditions for use in crossvalidation'''

    # parameters to be optimized

    params = [[], []]
    for lat_dist in model.lat_dist:  # variational widths
        param = lat_dist.gamma
        param.register_hook(hook)
        params[1].append(param)
    for manif in model.manif:  # varriational maens
        param = manif.mu
        param.register_hook(hook)
        params[0].append(param)
    for kernel in model.kernel.kernels:  # kerernels
        params[0].append(kernel.ell)
        params[0].append(kernel.alpha)
    for z in model.z:  # inducingg points
        params[0].append(z.z)

    params[0].append(model.sgp.sigma)  # noise variance

    return params


def svgp(Y,
         model,
         device,
         optimizer=optim.Adam,
         outdir="results/vanilla",
         max_steps=1000,
         n_mc=128,
         burnin=100,
         lrate=1E-3,
         callback=None,
         print_every=50,
         batch_size=None,
         n_svgp=0,
         ts=None,
        batch_pool = None,
        mask_Ts = None,
        neuron_idxs = None):
    '''
    batch_pool [optional] : None or int list
        pool of indices from which to batch (used to train a partial model)
    '''
    
    
    n, m, _ = Y.shape  # neurons, conditions, samples
    data = torch.from_numpy(Y).float().to(device)
    ts = ts if ts is None else ts.to(device)
    data_size = m  #total conditions
    n = n if neuron_idxs is None else len(neuron_idxs)

    def generate_batch_idxs(batch_pool = None):
        if batch_pool is None:
            idxs = np.arange(data_size)
        else:
            idxs = copy.copy(batch_pool)
            data_size = len(idxs)
        if model.lprior.name == "Brownian":
            # if prior is Brownian, then batches have to be contiguous

            i0 = np.random.randint(1, data_size - 1)
            if i0 < batch_size / 2:
                batch_idxs = idxs[:int(round(batch_size / 2 + i0))]
            elif i0 > (data_size - batch_size / 2):
                batch_idxs = idxs[int(round(i0 - batch_size / 2)):]
            else:
                batch_idxs = idxs[int(round(i0 - batch_size /
                                            2)):int(round(i0 +
                                                          batch_size / 2))]
            #print(len(batch_idxs))
            return batch_idxs

            #start = np.random.randint(data_size - batch_size)
            #return idxs[start:start + batch_size]
        else:
            np.random.shuffle(idxs)
            return idxs[0:batch_size]

    #optionally mask some time points
    if mask_Ts is None:
        def mask_Ts(grad):
            return grad

    params = sort_params(model, mask_Ts, True, svgp=True)
    opt = optimizer(params[0], lr=lrate)  # instantiate optimizer
    opt.add_param_group({'params': params[1]})
    opt.add_param_group({'params': params[2]})

    # set learning rate schedule so sigma updates have a burn-in period
    def fburn(x):
        return 1 - np.exp(-x / (3 * burnin))

    LRfuncs = [lambda x: 1, fburn, lambda x: 1]
    scheduler = LambdaLR(opt, lr_lambda=LRfuncs)

    for i in range(max_steps):  # come up with a different stopping condition
        opt.zero_grad()
        ramp = 1 - np.exp(-i / burnin)  # ramp the entropy

        if (batch_size is None and batch_pool is None):
            batch_idxs = None
        elif batch_size is None:
            batch_idxs = batch_pool
            m = len(batch_idxs)
        else:
            batch_idxs = generate_batch_idxs(batch_pool = batch_pool)
            m = len(batch_idxs)  #use for printing likelihoods etc.

        svgp_elbo, kl = model(data,
                            n_mc,
                            batch_idxs=batch_idxs,
                            ts=ts,
                             neuron_idxs = neuron_idxs)
            
        loss = (-svgp_elbo) + (ramp * kl)  # -LL
        loss.backward()
        opt.step()
        scheduler.step()
        if i % print_every == 0:
            mu_mag = np.mean(
                np.sqrt(
                    np.sum(model.lat_dist.manif.prms.data.cpu().numpy()[:]**2,
                           axis=1)))
            sig = np.median(
                np.concatenate([
                    np.diag(sig)
                    for sig in model.lat_dist.prms.data.cpu().numpy()
                ]))
            msg = ('\riter {:3d} | elbo {:.3f} | kl {:.3f} | loss {:.3f} ' +
                   '| |mu| {:.3f} | sig {:.3f} |').format(
                       i,
                       svgp_elbo.item() / (n * m),
                       kl.item() / (n * m),
                       loss.item() / (n * m), mu_mag, sig)
            print(msg + model.kernel.msg + model.lprior.msg, end="\r")

        if callback is not None:
            callback(model, i)

    return model
