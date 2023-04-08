#!/usr/bin/python3
import optas
import sys
import numpy as np
import pybullet as pb
import matplotlib.pyplot as plt
from time import sleep, time, perf_counter, time_ns
from scipy.spatial.transform import Rotation as Rot
from optas.spatialmath import *
import os

import rclpy
import xacro
from ament_index_python import get_package_share_directory
from rclpy import qos
from rclpy.node import Node

import pathlib

import urdf_parser_py.urdf as urdf



class Estimator(Node):
    def __init__(self, node_name = "para_estimatior", dt_ = 1.0) -> None:
        super().__init__(node_name=node_name)

        self.dt_ = dt_
        self.declare_parameter("model", "med7")
        self.model_ = str(self.get_parameter("model").value)
        path = os.path.join(
            get_package_share_directory("lbr_description"),
            "urdf",
            self.model_,
            f"{self.model_}.urdf.xacro",
        )


        # 1. Get the kinematic parameters of every joints
        self.robot = optas.RobotModel(
            xacro_filename=path,
            time_derivs=[1],  # i.e. joint velocity
        )
        root = self.robot.urdf.get_root()
        ee_link = "lbr_link_ee"
        xyzs, rpys, axes = [], [], []


        joints_list = self.robot.urdf.get_chain(root, ee_link, links=False)
        print("joints_list = {0}"
              .format(joints_list)
              )
        # assumption: The first joint is fixed. The information in this joint is not recorded
        """
        xyzs starts from joint 0 to joint ee
        rpys starts from joint 0 to joint ee
        axes starts from joint 0 to joint ee
        """
        joints_list_r = joints_list[1:]
        for joint_name in joints_list_r:
            print(joint_name)
            joint = self.robot.urdf.joint_map[joint_name]
            xyz, rpy = self.robot.get_joint_origin(joint)
            axis = self.robot.get_joint_axis(joint)

            # record the kinematic parameters
            xyzs.append(xyz)
            rpys.append(rpy)
            axes.append(axis)
        print("xyz, rpy, axis = {0}, {1} ,{2}".format(xyzs, rpys, axes))

        # 2. RNEA
        """
        input: q, qdot, qddot, model
        output: tau
        """
        Nb = self.robot.ndof
        self.Nb = Nb
        om0 = cs.DM([0.0,0.0,0.0])
        om0D = cs.DM([0.0,0.0,0.0])
        gravity_para = cs.DM([0.0, 0.0, -9.81])

        """
        The definination of joint position from joint0 to joint(Nb-1)
        """

        q = cs.SX.sym('q', Nb, 1)
        qd = cs.SX.sym('qd', Nb, 1)
        qdd = cs.SX.sym('qdd', Nb, 1)

        """
        The definination of mass for link1 to linkee
        The definination of center of Mass for link1 to linkee
        The definination of Inertial tensor for link1 to linkee
        """
        m = cs.SX.sym('m', 1, Nb+1)
        cm = cs.SX.sym('cm',3,Nb+1)
        Icm = cs.SX.sym('Icm',3,3*Nb+3)

        """
        external force given by link0
        The list will be appended via RNEA
        """
        fs = [cs.DM([0.0,0.0,0.0])]
        ns = [cs.DM([0.0,0.0,0.0])]


        """
        $$ Forward part of RNEA $$
        oms,omDs,vDs given by link0. The list will be appended via RNEA.
        Notes: the gravity_para represents a base acceration to subsitute the gravity.

        """
        oms = [om0]
        omDs = [om0D]
        vDs = [-gravity_para]
        
        
        # 2.1 forward part of RNEA
        """
        link0->1, link1->2 ...., link7->end-effector (for example)
        joint0,   joint1 ....
        
        """
        joints_list_r1 = joints_list_r
        for i in range(len(joints_list_r1)):
            
            if(i!=len(joints_list_r1)-1):
                # print(joints_list_r1)
                iRp = (rpy2r(rpys[i]) @ angvec2r(q[i], axes[i])).T
                iaxisi = iRp @ axes[i]
                omi = iRp @ oms[i] + iaxisi* qd[i]
                omDi = iRp @ omDs[i] +  iRp @skew(oms[i]) @ (iaxisi*qd[i]) + iaxisi*qdd[i]
            else:
                iRp = rpy2r(rpys[i]) 
                omi = iRp @ oms[i]
                omDi = iRp @ omDs[i]

            vDi = iRp @ (vDs[i] + skew(omDs[i]) @ xyzs[i]
                        + skew(oms[i]) @ skew(oms[i])@ xyzs[i])
            
            fi = m[i] * (vDi + skew(omDi)@ cm[:,i]+ skew(omi)@(skew(omi)@cm[:,i]))
            ni = Icm[:,i*3:i*3+3] @ omDi + skew(omi) @ Icm[:,i*3:i*3+3] @ omi

            

            oms.append(omi)
            omDs.append(omDi)
            vDs.append(vDi)
            fs.append(fi)
            ns.append(ni)


        """
        $$ Backward part of RNEA $$
        """

        pRi = rpy2r(rpys[-1])
        ifi = fs[-1]#cs.DM([0.0,0.0,0.0])
        ini = ns[-1] + skew(cm[:,-1]) @ fs[-1]#cs.DM([0.0,0.0,0.0])
        taus = []


        for i in range(len(joints_list_r)-1,0,-1):

            print("index = {0}".format(i))
            if(i < len(joints_list_r)-1):
                pRi = rpy2r(rpys[i]) @ angvec2r(q[i], axes[i])
            elif(i == len(joints_list_r)-1):
                pRi = rpy2r(rpys[i])
            else:
                pRi = rpy2r(rpys[i])
            

            ini = ns[i] + pRi @ ini +skew(cm[:,i-1]) @ fs[i] +skew(xyzs[i]) @ pRi @ifi
            ifi= pRi @ ifi + fs[i]
            pRi = rpy2r(rpys[i-1]) @ angvec2r(q[i-1], axes[i-1])
            _tau = ini.T @pRi.T @ axes[i-1]
            taus.append(_tau)


        
        tau_=cs.vertcat(*[taus[k] for k in range(len(taus)-1,-1,-1)])
        print(tau_.size())
        urdf_string_ = xacro.process(path)
        robot = urdf.URDF.from_xml_string(urdf_string_)
        print([joint.name for joint in robot.joints if joint.origin is not None])
        print([joint.origin.xyz for joint in robot.joints if joint.origin is not None])




        print([link.name for link in robot.links if link.inertial is not None])
        print([link.inertial.origin.xyz for link in robot.links if link.inertial is not None])
        print([link.inertial.mass for link in robot.links if link.inertial is not None])
        masses = [link.inertial.mass for link in robot.links if link.inertial is not None]#+[1.0]
        self.masses_np = np.array(masses[1:])
        print("masses = {0}".format(self.masses_np))

        massesCenter = [link.inertial.origin.xyz for link in robot.links if link.inertial is not None]#+[[0.0,0.0,0.0]]
        self.massesCenter_np = np.array(massesCenter[1:]).T
        Inertia = [np.mat(link.inertial.inertia.to_matrix()) for link in robot.links if link.inertial is not None]
        
        self.Inertia_np = np.hstack(tuple(Inertia[1:]))
        print("massesCenter = {0}".format(self.massesCenter_np))
        print("Inertia = {0}".format(self.Inertia_np))
        # print("Inertia = {0} , {1},{2}".format(np.size(self.Inertia_np,0),np.size(self.Inertia_np,1),np.size(self.Inertia_np,2)))

        # tau_ = tau
        
        self.dynamics_ = optas.Function('dynamics', [q,qd,qdd,m,cm,Icm], [tau_])
        # self.dynamics_i = optas.Function('dynamics1', [m,cm,Icm], [tau_])
        g_ =  self.dynamics_(q,np.zeros([Nb,1]),np.zeros([Nb,1]),m,cm,Icm)
        g1 =  self.dynamics_(q,np.zeros([Nb,1]),np.zeros([Nb,1]),m,cm,np.zeros([3,3*Nb+3]))
        g1_ = optas.simplify(g1)
        # g2_ =  self.dynamics_(q,np.zeros([Nb,1]),np.zeros([Nb,1]),m,cm,np.ones([3,3*Nb+3]))
        
        self.gra = optas.Function('gravity', [q,m,cm], [g1_])
        # print("g1_ = {0}".format(g1_[0]))

        """
        Get Inertia parameters set
        """
        dynamicsF_ = self.dynamics_(q,np.zeros([Nb,1]),np.zeros([Nb,1]),
                                    np.ones([Nb+1,1]),np.zeros([3,Nb+1]),np.zeros([3,3*Nb+3]))


        # print("dynamicsF_ = {0}".format(dynamicsF_))


        self.lbr_command_timer_ = self.create_timer(self.dt_, self.timer_cb_)
        
        # _numK = 10
        # for every torque
        Y = []
        
        for i in range(tau_.shape[0]):
            # for every link
            # Y_line = []
            Y_line = []
            # PI_a = []
            for j in range(m.shape[1]):
                # for every parameters
                # pi_temp = [m[j],
                #            m[j]*cm[0,j],
                #            m[j]*cm[1,j],
                #            m[j]*cm[2,j],
                #            Icm[0,0+3*j] + m[j]*(cm[1,j]*cm[1,j]+cm[2,j]*cm[2,j]),  # XXi
                #            Icm[0,1+3*j] - m[j]*(cm[0,j]*cm[1,j]),  # XYi
                #            Icm[0,2+3*j] - m[j]*(cm[0,j]*cm[2,j]),  # XZi
                #            Icm[1,1+3*j] + m[j]*(cm[0,j]*cm[0,j]+cm[2,j]*cm[2,j]),  # YYi
                #            Icm[1,2+3*j] - m[j]*(cm[1,j]*cm[2,j]),  # YZi
                #            Icm[2,2+3*j] + m[j]*(cm[0,j]*cm[0,j]+cm[1,j]*cm[1,j])] # ZZi
                # PI_a.append(pi_temp)
                ## 1. get mass
                m_indu = np.zeros([m.shape[1],m.shape[0]])
                cm_indu = np.zeros([3,Nb+1])#np.zeros([cm.shape[1],cm.shape[0]])
                Icm_indu = np.zeros([3,3*Nb+3])#np.zeros([Icm.shape[1],Icm.shape[0]])
                # print(*m.shape)
                m_indu[j] = 1.0
                print(m_indu)

                output = self.dynamics_(q,qd,qdd,m_indu,cm_indu,Icm_indu)[i]
                Y_line.append(output)


                ## 2. get cmx
                output1 = self.dynamics_(q,qd,qdd,m_indu,cm,Icm_indu)[i]-output
                for k in range(3):
                    output_cm = optas.jacobian(output1,cm[k,j])
                    output_cm1 = optas.substitute(output_cm,cm,cm_indu)
                    Y_line.append(output_cm1)

                ## 3.get Icm
                output2 = self.dynamics_(q,qd,qdd,m_indu,cm_indu,Icm)[i]-output
                for k in range(3):
                    for l in range(k,3,1):
                        output_Icm = optas.jacobian(output2,Icm[k,l+3*j])
                        Y_line.append(output_Icm)

                # sx_lst = optas.horzcat(*Y_seg)
                # Y_line
            sx_lst = optas.horzcat(*Y_line)
            Y.append(sx_lst)
            # print("Y_line shape = {0}, {1}".format(Y_line[0].shape[0],Y_line[0].shape[1]))
            # print("sx_lst shape = {0}, {1}".format(sx_lst.shape[0],sx_lst.shape[1]))

        Y_mat = optas.vertcat(*Y)
        # print(Y_mat)
        print("Y_mat shape = {0}, {1}".format(Y_mat.shape[0],Y_mat.shape[1]))
        self.Ymat = optas.Function('Dynamic_Ymat',[q,qd,qdd],[Y_mat])

        PI_a = []
        for j in range(m.shape[1]):
            # for every parameters
            pi_temp = [m[j],
                        m[j]*cm[0,j],
                        m[j]*cm[1,j],
                        m[j]*cm[2,j],
                        Icm[0,0+3*j] + m[j]*(cm[1,j]*cm[1,j]+cm[2,j]*cm[2,j]),  # XXi
                        Icm[0,1+3*j] - m[j]*(cm[0,j]*cm[1,j]),  # XYi
                        Icm[0,2+3*j] - m[j]*(cm[0,j]*cm[2,j]),  # XZi
                        Icm[1,1+3*j] + m[j]*(cm[0,j]*cm[0,j]+cm[2,j]*cm[2,j]),  # YYi
                        Icm[1,2+3*j] - m[j]*(cm[1,j]*cm[2,j]),  # YZi
                        Icm[2,2+3*j] + m[j]*(cm[0,j]*cm[0,j]+cm[1,j]*cm[1,j])] # ZZi
            PI_a.append(optas.vertcat(*pi_temp))

        PI_vecter = optas.vertcat(*PI_a)
        print("PI_vecter shape = {0}, {1}".format(PI_vecter.shape[0],PI_vecter.shape[1]))
        self.PIvector = optas.Function('Dynamic_PIvector',[m,cm,Icm],[PI_vecter])
        



                


            

                # output_cmx_2 = optas.jacobian(output_cmx,cm[0,j])/2


                
                # output = optas.jacobian(tau_[i],pi_temp[0])
                # print("output = {0}".format(output))
                # print(tau_.shape[0])
                # print(m.shape[1])
                
                # for k in range(len(pi_temp)):
                #     print(pi_temp[k])
                #     output = optas.jacobian(tau_[i],pi_temp[k])
                #     print(output)




        '''
        Setup PyBullet for checking RNEA
        '''

        pb.connect(*[pb.DIRECT])
        pb.resetSimulation()
        path2 = os.path.join(
            get_package_share_directory("lbr_description"),
            "urdf",
            self.model_,
        )
        pb.setAdditionalSearchPath(path2)

        gravz = -9.81
        pb.setGravity(0, 0, gravz)

        sampling_freq = 240
        time_step = 1./float(sampling_freq)
        pb.setTimeStep(time_step)
        pb.resetDebugVisualizerCamera(
            cameraDistance=0.2,
            cameraYaw=-180,
            cameraPitch=30,
            cameraTargetPosition=np.array([0.35, -0.2, 0.2]),
        )
        pb.configureDebugVisualizer(pb.COV_ENABLE_GUI, 0)

        self.id = pb.loadURDF(
            'med7.urdf',
            basePosition=[0, 0, 0],
        )
        self.iter = 0.0


        
    def timer_cb_(self) -> None:
        q_np = np.array([1.0, self.iter+3.14159/2, 1.0, 1.0, 0.0, 1.0, 3.0])
        qd_np = np.array([10.0, 9.0, 1.0, 2.0, 3.0, 5.0, 2.0])
        qdd_np = np.zeros(self.Nb)
        self.iter += 3.1415926535
        for i in range(self.Nb):
            # print("ccc = {0}".format(i))
            pb.resetJointState(self.id, i, q_np[i], qd_np[i])

        tau_ext = np.array(pb.calculateInverseDynamics(self.id, q_np.tolist(), qd_np.tolist(), qdd_np.tolist()))
        # t = self.gra(q_np,self.masses_np,self.massesCenter_np)
        t = self.dynamics_(q_np,qd_np,qdd_np,self.masses_np,self.massesCenter_np,self.Inertia_np)
        

        tt = self.Ymat(q_np,qd_np,qdd_np) @ self.PIvector(self.masses_np,self.massesCenter_np,self.Inertia_np)

        print("tau_ext = {0}\n tau_g = {1}".format(tau_ext,t))
        print("\n error = {0}\n ".format(tau_ext-t))

        print("tau_ext1 = {0}\n tau_g1 = {1}".format(tau_ext,tt))
        print("\n error1 = {0}\n ".format(tau_ext-tt))



        # print("Y_mat = {0}\n ".format(self.Ymat(q_np,qd_np,qdd_np)))
        








def main(args=None):
    rclpy.init(args=args)
    paraEstimator = Estimator()
    rclpy.spin(paraEstimator)
    rclpy.shutdown()



if __name__ == "__main__":
    main()