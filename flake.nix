{
  description = "funcd add-ons built outside funcd";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }: let
    supportedSystems = [ "aarch64-darwin" "x86_64-linux" "aarch64-linux" ];
    forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
  in {
    devShells = forAllSystems (system: let
      pkgs = import nixpkgs { inherit system; };
    in {
      # go builds the plugins and funcdctl for the e2e; node 22 runs the e2e's probe Function under funcdctl dev,
      # python 3.14 its harness, and docker-client talks to the runner's Docker
      default = pkgs.mkShellNoCC {
        packages = with pkgs; [
          go
          just
          git
          nodejs_22
          python314
          docker-client
        ];
      };
    });
  };
}
