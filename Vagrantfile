# -*- mode: ruby -*-
# vi: set ft=ruby :
#
# This defines a Ubuntu 16.04 based VM in which some code formatters are installed
# for testing whatstyle.

Vagrant.configure(2) do |config|

  config.vm.box = "ubuntu/xenial64"
  config.vm.box_check_update = false

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 2048
    vb.cpus = 4
    vb.name = nil
  end

  # Set disabled to false after provisioning.
  config.vm.synced_folder ".", "/vagrant", disabled: true

  config.vm.provision "shell", inline: <<-SHELL
    # Fix the "sudo: unable to resolve host ubuntu-xenial" message
    sudo sed -i -e "s/127.0.0.1 localhost$/127.0.0.1 localhost ubuntu-xenial/g" /etc/hosts

    sudo apt-get update

    # Install tools and formatters that come with Ubuntu
    sudo apt-get install -y autoconf cmake clang unzip python python-pip clang-format indent astyle

    # Install recent version of uncrustify
    export VERSION=0.63
    curl -L -O https://github.com/uncrustify/uncrustify/archive/uncrustify-${VERSION}.zip
    unzip uncrustify-${VERSION}.zip
    cd uncrustify-uncrustify-${VERSION}
    ./autogen.sh ; ./configure ; sudo make install

    # Compile tidy-html5 from source
    export VERSION=5.2.0
    curl -L -O https://github.com/htacg/tidy-html5/archive/${VERSION}.tar.gz
    sudo tar zxf ${VERSION}.tar.gz ; cd tidy-html5-${VERSION}; cmake . ; sudo make install

    # Install yapf
    sudo pip install yapf==0.10.0

    # Install VirtualBox Guest Additions kernel modules for the shared /vagrant directory.
    sudo apt-get install -y virtualbox-guest-dkms
  SHELL

end
