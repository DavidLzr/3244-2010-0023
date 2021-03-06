# recall-precision, inception score, ssim, psnr, (l1, l2)
# note that l1 and log like
import cv2
from skimage.metrics import structural_similarity as ssim

import torch
from torch import nn
from torch.autograd import Variable
from torch.nn import functional as F
import torch.utils.data

from torch.utils.data import TensorDataset, DataLoader
from torchvision.models.inception import inception_v3

import numpy as np
from scipy.stats import entropy

# imgs as numpy arrays; the_pytorch_tensor.numpy()
def ssim_score(img1, img2, data_range=1.0):
    img1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    img2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    return ssim(img1, img2, data_range=data_range)

# range [0,1] as in pytorch loaded
def psnr_score(img1, img2):
    # Ref from https://github.com/pytorch/examples/blob/master/super_resolution/main.py
    mse = np.mean((img1-img2) ** 2)
    return 10 * np.log10(1.0 / mse)

def inception_score(imgs, cuda=True, batch_size=32, resize=False, splits=1):
    """Computes the inception score of the generated images imgs
    imgs -- Torch dataset of (3xHxW) numpy images normalized in the range [-1, 1]
    cuda -- whether or not to run on GPU
    batch_size -- batch size for feeding into Inception v3
    splits -- number of splits
    Taken from https://github.com/sbarratt/inception-score-pytorch/blob/master/inception_score.py
    """
    N = len(imgs)

    assert batch_size > 0
    assert N > batch_size

    # Set up dtype
    if cuda:
        dtype = torch.cuda.FloatTensor
    else:
        if torch.cuda.is_available():
            print("WARNING: You have a CUDA device, so you should probably set cuda=True")
        dtype = torch.FloatTensor

    # Set up dataloader
    dataloader = torch.utils.data.DataLoader(imgs, batch_size=batch_size)

    # Load inception model
    inception_model = inception_v3(pretrained=True, transform_input=False).type(dtype)
    inception_model.eval();
    up = nn.Upsample(size=(299, 299), mode='bilinear').type(dtype)
    def get_pred(x):
        if resize:
            x = up(x)
        x = inception_model(x)
        return F.softmax(x).data.cpu().numpy()

    # Get predictions
    preds = np.zeros((N, 1000))

    for i, batch in enumerate(dataloader, 0):
        batch = batch.type(dtype)
        batchv = Variable(batch)
        batch_size_i = batch.size()[0]

        preds[i*batch_size:i*batch_size + batch_size_i] = get_pred(batchv)

    # Now compute the mean kl-div
    split_scores = []

    for k in range(splits):
        part = preds[k * (N // splits): (k+1) * (N // splits), :]
        py = np.mean(part, axis=0)
        scores = []
        for i in range(part.shape[0]):
            pyx = part[i, :]
            scores.append(entropy(pyx, py))
        split_scores.append(np.exp(np.mean(scores)))

    return np.mean(split_scores), np.std(split_scores)

def convertImgsListToTorchDataset(imgsList):

    tensor_x = torch.Tensor(imgsList) # transform to torch tensor

    my_dataset = TensorDataset(tensor_x) # create your datset
    my_dataloader = DataLoader(my_dataset) # create your dataloader
    
    return my_dataloader
