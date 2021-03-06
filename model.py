import itertools
import functools
import datetime

import os
import numpy as np
import cv2
import torch
import torchvision
from torch import nn
from torch.autograd import Variable
import torchvision.datasets as dsets
import torchvision.transforms as transforms
from torch.optim import lr_scheduler
from torch.utils.tensorboard import SummaryWriter

import utils
from generators import define_Gen
from discriminators import define_Dis
from ops import set_grad


class cycleGAN(object):
    def __init__(self, args):
        # Define the network
        #####################################################
        '''
        Define the network:
        Two generators: Gab, Gba
        Two discriminators: Da, Db
        '''
        self.Gab = define_Gen(input_nc=3, output_nc=3, ngf=args.ngf, netG=args.gen_net, norm=args.norm,
                              use_dropout=not args.no_dropout, gpu_ids=args.gpu_ids)
        self.Gba = define_Gen(input_nc=3, output_nc=3, ngf=args.ngf, netG=args.gen_net, norm=args.norm,
                              use_dropout=not args.no_dropout, gpu_ids=args.gpu_ids)
        self.Da = define_Dis(input_nc=3, ndf=args.ndf, netD=args.dis_net, n_layers_D=3, norm=args.norm,
                             gpu_ids=args.gpu_ids)
        self.Db = define_Dis(input_nc=3, ndf=args.ndf, netD=args.dis_net, n_layers_D=3, norm=args.norm,
                             gpu_ids=args.gpu_ids)

        utils.print_networks([self.Gab, self.Gba, self.Da, self.Db], ['Gab', 'Gba', 'Da', 'Db'])

        # Define loss criteria
        self.identity_criteron = nn.L1Loss()
        self.adversarial_criteron = nn.MSELoss()
        self.cycle_consistency_criteron = nn.L1Loss()

        # Define optimizers
        self.g_optimizer = torch.optim.Adam(itertools.chain(self.Gab.parameters(), self.Gba.parameters()), lr=args.lr,
                                            betas=(0.5, 0.999))
        self.d_optimizer = torch.optim.Adam(itertools.chain(self.Da.parameters(), self.Db.parameters()), lr=args.lr,
                                            betas=(0.5, 0.999))

        # Define learning rate schedulers
        self.g_lr_scheduler = torch.optim.lr_scheduler.LambdaLR(self.g_optimizer,
                                                                lr_lambda=utils.LambdaLR(args.epochs, 0,
                                                                                         args.decay_epoch).step)
        self.d_lr_scheduler = torch.optim.lr_scheduler.LambdaLR(self.d_optimizer,
                                                                lr_lambda=utils.LambdaLR(args.epochs, 0,
                                                                                         args.decay_epoch).step)

        # Try loading checkpoint
        #####################################################
        if not os.path.isdir(args.checkpoint_dir):
            os.makedirs(args.checkpoint_dir)

        try:
            ckpt = utils.load_checkpoint('%s/latest.ckpt' % (args.checkpoint_dir))
            self.start_epoch = ckpt['epoch']
            self.Da.load_state_dict(ckpt['Da'])
            self.Db.load_state_dict(ckpt['Db'])
            self.Gab.load_state_dict(ckpt['Gab'])
            self.Gba.load_state_dict(ckpt['Gba'])
            self.d_optimizer.load_state_dict(ckpt['d_optimizer'])
            self.g_optimizer.load_state_dict(ckpt['g_optimizer'])
        except:
            print(' [*] No checkpoint!')
            self.start_epoch = 0

        # Tensorboard Setup
        # current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        current_time = '20201024-102158'
        train_log_dir = 'logs/sketch2pokemon/' + current_time
        self.writer = SummaryWriter(train_log_dir)

        # Stability variables setup
        self.last_test_output = []
        self.cur_test_output = []

    def save_sample_image(self, test_length, a_test_loader, b_test_loader, results_dir, epoch):
        temp_dir = './temp'
        itera = iter(a_test_loader)
        iterb = iter(b_test_loader)
        res = []
        average_hist_score_correlation = 0.0
        average_hist_score_chi_square = 0.0
        average_hist_score_intersction = 0.0
        average_hist_score_bhat = 0.0
        for _ in range(test_length):
            a = itera.next()
            b = iterb.next()
            a_real_test = Variable(a[0], requires_grad=True)
            b_real_test = Variable(b[0], requires_grad=True)
            a_real_test, b_real_test = utils.cuda([a_real_test, b_real_test])
            self.Gab.eval()
            self.Gba.eval()

            with torch.no_grad():
                # a_fake_test = self.Gab(b_real_test)
                b_fake_test = self.Gba(a_real_test)
                # a_recon_test = self.Gab(b_fake_test)
                # b_recon_test = self.Gba(a_fake_test)

                res.extend([a_real_test, b_fake_test, b_real_test])
                self.cur_test_output.append(b_fake_test)
                
                ##### Generate histogram similarity score
                # Documentation: https://docs.opencv.org/3.4/d8/dc8/tutorial_histogram_comparison.html
                torchvision.utils.save_image(b_fake_test.cpu(), temp_dir + '/model_output.jpg')
                torchvision.utils.save_image(b_real_test.cpu(), temp_dir + '/groundtruth.jpg')
                src_base = cv2.imread(temp_dir + '/groundtruth.jpg')
                src_test1 = cv2.imread(temp_dir + '/model_output.jpg')
                hsv_base = cv2.cvtColor(src_base, cv2.COLOR_BGR2HSV)
                hsv_test1 = cv2.cvtColor(src_test1, cv2.COLOR_BGR2HSV)
                h_bins = 50
                s_bins = 60
                v_bins = 60
                histSize = [h_bins, s_bins, v_bins]
                # hue varies from 0 to 179, saturation from 0 to 255, value from 0 to 255
                h_ranges = [0, 180]
                s_ranges = [0, 256]
                v_ranges = [0, 256]
                ranges = h_ranges + s_ranges + v_ranges# concat lists
                # Use 0,1,2nd channels
                channels = [0, 1, 2]
                hist_base = cv2.calcHist([hsv_base], channels, None, histSize, ranges, accumulate=False)
                cv2.normalize(hist_base, hist_base, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
                hist_test1 = cv2.calcHist([hsv_test1], channels, None, histSize, ranges, accumulate=False)
                cv2.normalize(hist_test1, hist_test1, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
                # compare_method = {0: 'correlation', 1: 'chi-square', 2: 'intersection', 3: 'Bhattacharyya'}
                average_hist_score_correlation += cv2.compareHist(hist_base, hist_test1, 0)
                average_hist_score_chi_square += cv2.compareHist(hist_base, hist_test1, 1)
                average_hist_score_intersction += cv2.compareHist(hist_base, hist_test1, 2)
                average_hist_score_bhat += cv2.compareHist(hist_base, hist_test1, 3)
                # os.remove(temp_dir + '/model_output.jpg')
                # os.remove(temp_dir + '/groundtruth.jpg')
        self.writer.add_scalar('HistCompare_correlation', average_hist_score_correlation / test_length, epoch)
        self.writer.add_scalar('HistCompare_chi_square', average_hist_score_chi_square / test_length, epoch)
        self.writer.add_scalar('HistCompare_intersction', average_hist_score_intersction / test_length, epoch)
        self.writer.add_scalar('HistCompare_bhat', average_hist_score_bhat / test_length, epoch)
        
        if len(self.last_test_output) > 0:
            _sum = 0.0
            for i in range(test_length):
                _sum += np.linalg.norm(np.abs(self.last_test_output[i].cpu() - self.cur_test_output[i].cpu()))
            self.writer.add_scalar('L1 dist against last epoch', _sum / test_length, epoch)
        self.last_test_output = []
        for item in self.cur_test_output:
            self.last_test_output.append(item)
        self.cur_test_output = []


        pic = (torch.cat(res, dim=0).data + 1) / 2.0

        if not os.path.isdir(results_dir):
            os.makedirs(results_dir)
        self.writer.add_image(f'test_image', pic, epoch, dataformats='NCHW')
        torchvision.utils.save_image(pic, results_dir + '/result_%d.jpg' % epoch, nrow=6)

    def train(self, args):
        # For transforming the input image
        transform = transforms.Compose(
            [transforms.RandomHorizontalFlip(),
             transforms.Resize((args.load_height, args.load_width)),
             transforms.RandomCrop((args.crop_height, args.crop_width)),
             transforms.ToTensor(),
             transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])])

        test_transform = transforms.Compose(
            [transforms.Resize((args.test_crop_height, args.test_crop_width)),
             transforms.ToTensor(),
             transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])])

        dataset_dirs = utils.get_traindata_link(args.dataset_dir)
        testset_dirs = utils.get_testdata_link(args.dataset_dir)

        # Pytorch dataloader
        a_loader = torch.utils.data.DataLoader(dsets.ImageFolder(dataset_dirs['trainA'], transform=transform),
                                               batch_size=args.batch_size, shuffle=True, num_workers=4)
        b_loader = torch.utils.data.DataLoader(dsets.ImageFolder(dataset_dirs['trainB'], transform=transform),
                                               batch_size=args.batch_size, shuffle=True, num_workers=4)

        a_test_loader = torch.utils.data.DataLoader(dsets.ImageFolder(testset_dirs['testA'], transform=test_transform),
                                                    batch_size=1, shuffle=False, num_workers=4)
        b_test_loader = torch.utils.data.DataLoader(dsets.ImageFolder(testset_dirs['testB'], transform=test_transform),
                                                    batch_size=1, shuffle=False, num_workers=4)

        a_fake_sample = utils.Sample_from_Pool()
        b_fake_sample = utils.Sample_from_Pool()

        for epoch in range(self.start_epoch, args.epochs):

            if epoch >= 1:
                print('generating test result...')
                self.save_sample_image(args.test_length, a_test_loader, b_test_loader, args.results_dir, epoch)

            lr = self.g_optimizer.param_groups[0]['lr']
            print('learning rate = %.7f' % lr)
            running_Gen_loss = 0
            running_Dis_loss = 0
            ##################################################
            # BEGIN TRAINING FOR ONE EPOCH
            ##################################################
            for i, (a_real, b_real) in enumerate(zip(a_loader, b_loader)):
                # step
                step = epoch * min(len(a_loader), len(b_loader)) + i + 1

                ##################################################
                # Part 1: Generator Computations
                ##################################################

                set_grad([self.Da, self.Db], False)
                self.g_optimizer.zero_grad()

                a_real = Variable(a_real[0])
                b_real = Variable(b_real[0])
                a_real, b_real = utils.cuda([a_real, b_real])

                # Forward pass through generators
                ##################################################
                a_fake = self.Gab(b_real)
                b_fake = self.Gba(a_real)

                a_recon = self.Gab(b_fake)
                b_recon = self.Gba(a_fake)

                a_idt = self.Gab(a_real)
                b_idt = self.Gba(b_real)

                # Identity losses
                ###################################################
                a_idt_loss = self.identity_criteron(a_idt, a_real) * args.lamda * args.idt_coef
                b_idt_loss = self.identity_criteron(b_idt, b_real) * args.lamda * args.idt_coef
                # a_idt_loss = 0
                # b_idt_loss = 0

                # Adversarial losses
                ###################################################
                a_fake_dis = self.Da(a_fake)
                b_fake_dis = self.Db(b_fake)

                real_label = utils.cuda(Variable(torch.ones(a_fake_dis.size())))

                a_gen_loss = self.adversarial_criteron(a_fake_dis, real_label)
                b_gen_loss = self.adversarial_criteron(b_fake_dis, real_label)

                # Cycle consistency losses
                ###################################################
                a_cycle_loss = self.cycle_consistency_criteron(a_recon, a_real) * args.lamda
                b_cycle_loss = self.cycle_consistency_criteron(b_recon, b_real) * args.lamda

                # Total generators losses
                ###################################################
                gen_loss = a_gen_loss + b_gen_loss + a_cycle_loss + b_cycle_loss + a_idt_loss + b_idt_loss

                # Update generators
                ###################################################
                gen_loss.backward()
                self.g_optimizer.step()

                ##################################################
                # Part 2: Discriminator Computations
                #################################################

                set_grad([self.Da, self.Db], True)
                self.d_optimizer.zero_grad()

                # Sample from history of generated images
                #################################################
                a_fake = Variable(torch.Tensor(a_fake_sample([a_fake.cpu().data.numpy()])[0]))
                b_fake = Variable(torch.Tensor(b_fake_sample([b_fake.cpu().data.numpy()])[0]))
                a_fake, b_fake = utils.cuda([a_fake, b_fake])

                # Forward pass through discriminators
                #################################################
                a_real_dis = self.Da(a_real)
                a_fake_dis = self.Da(a_fake)
                b_real_dis = self.Db(b_real)
                b_fake_dis = self.Db(b_fake)
                real_label = utils.cuda(Variable(torch.ones(a_real_dis.size())))
                fake_label = utils.cuda(Variable(torch.zeros(a_fake_dis.size())))

                # Discriminator losses
                ##################################################
                a_dis_real_loss = self.adversarial_criteron(a_real_dis, real_label)
                a_dis_fake_loss = self.adversarial_criteron(a_fake_dis, fake_label)
                b_dis_real_loss = self.adversarial_criteron(b_real_dis, real_label)
                b_dis_fake_loss = self.adversarial_criteron(b_fake_dis, fake_label)

                # Total discriminators losses
                a_dis_loss = (a_dis_real_loss + a_dis_fake_loss) * 0.5
                b_dis_loss = (b_dis_real_loss + b_dis_fake_loss) * 0.5

                # Update discriminators
                ##################################################
                a_dis_loss.backward()
                b_dis_loss.backward()
                self.d_optimizer.step()

                print("Epoch: (%3d) (%5d/%5d) | Gen Loss:%.2e | Dis Loss:%.2e" %
                      (epoch, i + 1, min(len(a_loader), len(b_loader)),
                       gen_loss, a_dis_loss + b_dis_loss))
                running_Gen_loss += gen_loss
                running_Dis_loss += (a_dis_loss + b_dis_loss)
            ##################################################
            # END TRAINING FOR ONE EPOCH
            ##################################################
            self.writer.add_scalar('Gen Loss', running_Gen_loss / min(len(a_loader), len(b_loader)), epoch)
            self.writer.add_scalar('Dis Loss', running_Dis_loss / min(len(a_loader), len(b_loader)), epoch)
            self.writer.add_scalar('Gen_LR', self.g_lr_scheduler.get_lr()[0], epoch)
            self.writer.add_scalar('Dis_LR', self.d_lr_scheduler.get_lr()[0], epoch)
            # Override the latest checkpoint
            #######################################################
            utils.save_checkpoint({'epoch': epoch + 1,
                                   'Da': self.Da.state_dict(),
                                   'Db': self.Db.state_dict(),
                                   'Gab': self.Gab.state_dict(),
                                   'Gba': self.Gba.state_dict(),
                                   'd_optimizer': self.d_optimizer.state_dict(),
                                   'g_optimizer': self.g_optimizer.state_dict()},
                                  '%s/latest.ckpt' % (args.checkpoint_dir))

            # Update learning rates
            ########################
            self.g_lr_scheduler.step()
            self.d_lr_scheduler.step()

        self.writer.close()
